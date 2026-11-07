"""How much text fits one Ollama request, and how large the window may be.

Two decisions live here, both pure:

* :class:`ContextBudget` — how many characters a request may carry, given the model's
  context window (``num_ctx``) and how long the answer is going to be.  Ollama silently
  drops the middle of a prompt that is longer than its window, so the size is checked by
  estimate before a request goes out.
* :func:`automatic_num_ctx` / :func:`run_windows` — which window a computer actually gets.
  A larger window raises the limit; it must never turn a model rated "Works" into one rated
  "Slower", so it is picked from the model's memory need against the graphics card and only
  from the fixed ladder :data:`CONTEXT_STEPS` (a window that changes between files would make
  Ollama reload the model for every file).

Nothing here does I/O: the infrastructure layer reads the model sizes, this module decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_catalog import OllamaModelSpec

#: The context windows the app may pick, in tokens, smallest first.  A ladder rather than an
#: arbitrary size, so only a model or a settings change can make Ollama reload the model.
CONTEXT_STEPS: tuple[int, ...] = (8192, 16384, 32768)

#: The smallest step: what every computer gets, and what a model with unmeasured context
#: growth keeps.
BASE_NUM_CTX = CONTEXT_STEPS[0]

#: Largest window used for proofreading.  A review answer repeats the text it was given, and
#: small models drift or start to loop on long answers, so the window stops growing early.
PROOFREADING_NUM_CTX_CAP = 16384

#: Largest window used for a content review.  Its answer is short and it cannot be split into
#: pieces, so it is the stage that gains the most from a larger window.
EVALUATION_NUM_CTX_CAP = 32768

#: Tokens reserved for the answer of a content review.  A review answers with a short
#: assessment, not with the text again, so it needs a fixed allowance instead of "as much as
#: the request".  Unmeasured: an estimate, deliberately on the generous side.
EVALUATION_ANSWER_TOKENS = 1536


#: How long a structured review answer is compared with the text it was given.  For every
#: changed sentence it carries the original wording, the corrected wording and a list of
#: changes with explanations, plus the JSON keys, so on an error-dense text it is well over
#: twice as long.  An estimate, deliberately cautious; text mode answers with about the text.
REVIEW_ANSWER_FACTOR = 2.0


def answers_with_the_same_text(num_ctx: int) -> int:
    """Return the answer allowance (tokens) of a request whose answer repeats the text.

    Half of the window: the request may take the other half.  That is the rule for
    proofreading (structured review) and for text mode.
    """
    return num_ctx // 2


@dataclass(frozen=True)
class ContextBudget:
    """How much text one request may carry in a window of *num_ctx* tokens.

    Two shapes of answer are supported:

    * it is as long as, or longer than, the text it was given (proofreading) — pass
      ``answer_factor``: ``1.0`` lets the request take half of the window (text mode), and
      :data:`REVIEW_ANSWER_FACTOR` a third (a review answer is longer than the text);
    * it is short and of a known length (a content review) — pass ``answer_tokens``.

    The estimate is deliberately cautious: :data:`DEFAULT_CHARS_PER_TOKEN` characters per
    token, fewer than German prose really needs.
    """

    num_ctx: int
    #: Characters per token assumed.  Measured on German, the longest of the three languages:
    #: about 4 for clean prose, fewer for misspelt words.  3 errs on the safe side.
    chars_per_token: float = 3.0

    def request_fits(self, request_chars: int, *, answer_tokens: int) -> bool:
        """Return whether a request of *request_chars* characters fits next to the answer."""
        request_tokens = -(-request_chars // int(self.chars_per_token))  # ceiling division
        return request_tokens + answer_tokens <= self.num_ctx

    def max_text_chars(
        self,
        overhead_chars: int,
        *,
        answer_factor: float = 0.0,
        answer_tokens: int = 0,
    ) -> int:
        """Return how many characters of text fit next to *overhead_chars*.

        Args:
            overhead_chars: Characters that go out with every request (system prompt plus the
                fixed part of the question).
            answer_factor: How long the answer is compared to the request; ``1.0`` means "as
                long as the text it was given" (proofreading).
            answer_tokens: Tokens reserved for a short answer of a known length; used when
                *answer_factor* is 0 (a content review).
        """
        available_tokens = self.num_ctx - answer_tokens
        if answer_factor > 0:
            available_tokens = int(self.num_ctx / (1.0 + answer_factor))
        available_chars = available_tokens * self.chars_per_token
        return max(0, int(available_chars - overhead_chars))


def memory_at_context_gb(spec: OllamaModelSpec | None, num_ctx: int) -> float | None:
    """Return the memory (GB) *spec* needs at *num_ctx*, or ``None`` when unknown.

    ``context_gb_per_8k`` is measured, because how fast a context cache grows depends on the
    model's attention design (sliding-window or linear-attention layers grow much more slowly)
    and cannot be derived from the model size.  A model without that figure keeps
    :data:`BASE_NUM_CTX`.
    """
    if spec is None:
        return None
    measured = spec.context_gb_per_8k
    if measured is None or num_ctx <= BASE_NUM_CTX:
        return spec.memory_gb
    extra = (num_ctx - BASE_NUM_CTX) / BASE_NUM_CTX
    return spec.memory_gb + measured * extra


def _largest_step_within(
    spec: OllamaModelSpec | None,
    card_gb: float | None,
    *,
    cap: int,
    full_speed_share: float,
) -> int:
    """Return the largest ladder step whose memory need still fits *card_gb* at full speed.

    ``card_gb`` is ``None`` or 0 when there is no card to grow into (a computer without one, a
    remote server, a profile chosen by hand whose card size is unknown): the base window.
    """
    if spec is None or spec.context_gb_per_8k is None or card_gb is None or card_gb <= 0:
        return BASE_NUM_CTX
    chosen = BASE_NUM_CTX
    for step in CONTEXT_STEPS:
        if step > cap:
            break
        need = memory_at_context_gb(spec, step)
        assert need is not None  # the measurement exists (checked above)
        if card_gb / need < full_speed_share:
            break
        chosen = step
    return chosen


def automatic_num_ctx(
    spec: OllamaModelSpec | None,
    card_gb: float | None,
    *,
    cap: int,
    full_speed_share: float,
) -> int:
    """Return the context window (tokens) a stage gets on this computer without a setting.

    The largest :data:`CONTEXT_STEPS` entry at which the model still uses at most
    ``1 / full_speed_share`` of the graphics card, so a larger window can never turn a model
    rated "Works" into one rated "Slower".  Capped at *cap*: :data:`PROOFREADING_NUM_CTX_CAP`
    for proofreading (a review answer repeats the text and small models drift on long
    answers), :data:`EVALUATION_NUM_CTX_CAP` for a content review (its answer is short and the
    text cannot be split, so that stage gains the most).
    """
    return _largest_step_within(spec, card_gb, cap=cap, full_speed_share=full_speed_share)


def requested_num_ctx(
    setting: str,
    *,
    spec: OllamaModelSpec | None,
    card_gb: float | None,
    cap: int,
    full_speed_share: float,
) -> int:
    """Return the window to use: the ``proofreading_context`` setting, or the automatic rule.

    ``"auto"`` asks :func:`automatic_num_ctx`.  A fixed number from :data:`CONTEXT_STEPS` is
    what an advanced user (or a remote server whose card the app cannot see) asked for, and is
    used as it is, up to *cap*.  Anything unknown falls back to ``"auto"`` — callers warn about
    that where the setting is read.
    """
    fixed = pinned_num_ctx(setting)
    if fixed is not None:
        return min(fixed, cap)
    return automatic_num_ctx(spec, card_gb, cap=cap, full_speed_share=full_speed_share)


def pinned_num_ctx(setting: str) -> int | None:
    """Return the window *setting* pins, or ``None`` for ``"auto"``.

    Used where the window is needed but the hardware is not at hand (the cache key): a pinned
    window is a property of the settings alone, an automatic one is not.
    """
    try:
        fixed = int(setting)
    except (TypeError, ValueError):
        return None
    return fixed if fixed in CONTEXT_STEPS else None


def window_from_setting(setting: str) -> int:
    """Return the proofreading window the settings alone determine.

    The pinned window up to :data:`PROOFREADING_NUM_CTX_CAP` — what a run really uses, so two
    settings that give the same window give the same cache key — else the base step.
    """
    fixed = pinned_num_ctx(setting)
    return min(fixed, PROOFREADING_NUM_CTX_CAP) if fixed is not None else BASE_NUM_CTX


@dataclass(frozen=True)
class RunWindows:
    """The context windows one run uses, resolved once for the whole run.

    Proofreading and content review may use different windows (a review's answer is short and
    its text cannot be split, so it gains more from a larger one).  When both stages use the
    same model, Ollama would reload it between the two stages for every file, which costs
    seconds per file; there both use the smaller window, and content review still gains from its
    own answer allowance.
    """

    proofreading: int
    evaluation: int


def run_windows(
    setting: str,
    *,
    proofreading_spec: OllamaModelSpec | None,
    evaluation_spec: OllamaModelSpec | None,
    card_gb: float | None,
    same_model: bool,
    full_speed_share: float,
) -> RunWindows:
    """Return the two windows of a run: proofreading and content review.

    Each stage is sized from its own model: a content review on a larger model than the one
    that proofreads must not get a window that only the smaller model fits.

    Args:
        setting: The ``proofreading_context`` setting (``"auto"`` or a fixed window).
        proofreading_spec: Catalog entry of the proofreading model, or ``None`` for a model the
            user added.
        evaluation_spec: The same for the content-review model.
        card_gb: Graphics memory the models can use, or ``None``/0 when there is no card to see
            (a computer without one, a remote server, a hand-picked profile).
        same_model: Whether both stages run on the same Ollama model.
        full_speed_share: Share of the card at which a model still runs at full speed.
    """
    proofreading = requested_num_ctx(
        setting,
        spec=proofreading_spec,
        card_gb=card_gb,
        cap=PROOFREADING_NUM_CTX_CAP,
        full_speed_share=full_speed_share,
    )
    evaluation = requested_num_ctx(
        setting,
        spec=evaluation_spec,
        card_gb=card_gb,
        cap=EVALUATION_NUM_CTX_CAP,
        full_speed_share=full_speed_share,
    )
    if same_model:
        # No reload between the stages: the smaller window serves both.
        evaluation = min(proofreading, evaluation)
    return RunWindows(proofreading=proofreading, evaluation=evaluation)
