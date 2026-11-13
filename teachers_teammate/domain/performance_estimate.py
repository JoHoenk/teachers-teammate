"""How long a run takes on this computer: rates, answer lengths, ranges and bands.

A run's time is the sum of its requests, and one request costs

    prompt tokens / reading rate + answer tokens / writing rate

so the estimate needs three things per model: how fast it reads, how fast it writes, and how
long its answers are.  The first two are properties of the machine and the model together and
are **measured** (the speed test, then real runs); the third is a property of the model and the
task and comes from the catalog (:class:`~.model_catalog.AnswerLength`).

Two rules shape everything here:

* **Estimates are ranges, never a single number.**  The middle 80 % of answer lengths of one
  model on one test set are about three times apart, so a single figure would be a promise the
  app cannot keep.  Every function returns a :class:`TimeRange`.
* **The test measures the machine, not the answer length.**  How many errors a class makes is
  not something a test can know; the catalog and real runs answer that.

Nothing here does I/O and nothing here formats a duration: the GUI turns seconds into
"about 1 h 30 min to 3 h".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import math
from statistics import median

from .model_catalog import OLLAMA_CATALOG, AnswerLength, ModelCategory, OllamaModelSpec

#: Words on a typical page of a student's essay.  Used where a page has to be turned into a
#: word count (a proofreading estimate for "one page").  An estimate: a handwritten A4 page of
#: an essay holds 200 to 300 words.
TYPICAL_PAGE_WORDS = 250

#: Characters per word, the space included.  German prose is the longest of the three languages
#: the app is used for (about 6.2), English the shortest (about 5.5).
CHARS_PER_WORD = 6.0

#: Characters per token assumed until the speed test has measured the model.  Between the
#: window budget's cautious 3 and the 4.3 to 5 measured on German prose at page length.
DEFAULT_CHARS_PER_TOKEN = 4.0

#: Tokens of one page image, until the speed test has measured the reading model.  A full A4
#: page at 300 DPI is what the app sends; how many tokens a vision model makes of it depends on
#: its own image encoder.  An estimate.
DEFAULT_IMAGE_TOKENS = 1500

#: Answer tokens per 100 words of student text in **text mode** (corrections only).  Measured
#: with ``gemma4:e2b`` over 9 texts on 2026-11-13: median 141, 10th to 90th percentile 120 to
#: 151.  Text mode answers with about the text's own length, so one figure serves every model.
TEXT_MODE_ANSWER = AnswerLength(low=120, typical=141, high=151)

#: Answer tokens per 100 words of student text for a **content review**.  A review answers with
#: a short assessment, not with the text again, and its request reserves
#: :data:`~.context_budget.EVALUATION_ANSWER_TOKENS` at most.  An estimate, deliberately wide.
CONTENT_REVIEW_ANSWER = AnswerLength(low=80, typical=120, high=200)

#: Answer tokens per page of a reading model, used while no catalogued reading model has a
#: measured figure.  A page of about 250 words plus the markup a reading model adds.  An
#: estimate.
DEFAULT_READING_ANSWER = AnswerLength(low=350, typical=600, high=1100)


class SpeedBand(StrEnum):
    """How long one page takes for one step, in words a teacher can act on."""

    FAST = "fast"
    """Up to :data:`FAST_LIMIT_S` seconds per page."""
    OK = "ok"
    """Up to :data:`OK_LIMIT_S` seconds per page."""
    SLOW = "slow"
    """Up to :data:`SLOW_LIMIT_S` seconds per page."""
    VERY_SLOW = "very_slow"
    """More than :data:`SLOW_LIMIT_S` seconds per page."""


#: Band limits in seconds per page.  They match what is already measured: on an 8 GB card a page
#: took under a minute at full speed and about seven at the bottom (see
#: :data:`~.model_catalog.FULL_SPEED_SHARE`).
FAST_LIMIT_S = 30.0
OK_LIMIT_S = 90.0
SLOW_LIMIT_S = 300.0


class SpeedSource(StrEnum):
    """Where a step's estimate comes from."""

    MEASURED = "measured"
    """The speed test or real runs measured this model on this computer."""
    EXPECTED = "expected"
    """The model was never measured here; its speed is expected from the reference machine."""


class SpeedStep(StrEnum):
    """The steps whose speed is measured, in the order the test measures them."""

    READING = "reading"
    PROOFREADING = "proofreading"
    REVIEW = "review"


class StepOutcome(StrEnum):
    """What became of one step of a measurement."""

    MEASURED = "measured"
    TOO_SLOW = "too_slow"
    """The request hit its time limit; whatever is known is kept."""
    SKIPPED = "skipped"
    """The model is not installed on the server, so there was nothing to measure."""
    FAILED = "failed"
    """The request failed for another reason (a refused window, an empty answer)."""


@dataclass(frozen=True)
class ModelSpeed:
    """One model on one machine, as the speed test or real runs measured it."""

    read_tps: float
    """Tokens of the request read per second."""
    write_tps: float
    """Tokens of the answer written per second."""
    load_s: float | None = None
    """Seconds to load the model; ``None`` when it was already loaded, so the time is unknown."""
    on_card: float | None = None
    """Share (0 to 1) of the model that sits on the graphics card, from ``/api/ps``.

    A hint, except for 0: Ollama's size figures are wrong for some models, but a zero is
    unambiguous (see :func:`card_not_used`).
    """
    image_tokens: int | None = None
    """Reading models: tokens of one page image, as the test page measured them."""
    chars_per_token: float | None = None
    """Writing models: characters of the request per token, measured on the test paragraph."""


@dataclass(frozen=True)
class TimeRange:
    """A span of seconds: the low and the high end of an estimate."""

    low_s: float
    high_s: float

    def __add__(self, other: TimeRange) -> TimeRange:
        """Return the range of doing both things one after the other."""
        return TimeRange(self.low_s + other.low_s, self.high_s + other.high_s)

    def scaled(self, factor: float) -> TimeRange:
        """Return this range multiplied by *factor*."""
        return TimeRange(self.low_s * factor, self.high_s * factor)

    @property
    def typical_s(self) -> float:
        """Return the middle of the range, for a band or a single figure."""
        return (self.low_s + self.high_s) / 2


def _chars_per_token(speed: ModelSpeed) -> float:
    """Return the measured characters per token of *speed*, else the default."""
    measured = speed.chars_per_token
    return measured if measured and measured > 0 else DEFAULT_CHARS_PER_TOKEN


def _prompt_seconds(speed: ModelSpeed, chars: float) -> float:
    """Return the seconds *speed* needs to read a request of *chars* characters."""
    return chars / _chars_per_token(speed) / speed.read_tps


def reading_page_time(speed: ModelSpeed, answer: AnswerLength) -> TimeRange:
    """Return how long one page takes a reading model.

    The image is read once (its tokens over the reading rate); the answer is written token by
    token, and its length is what the range is about.
    """
    image_tokens = speed.image_tokens or DEFAULT_IMAGE_TOKENS
    read_s = image_tokens / speed.read_tps
    return TimeRange(
        low_s=read_s + answer.low / speed.write_tps,
        high_s=read_s + answer.high / speed.write_tps,
    )


def proofreading_time(
    speed: ModelSpeed,
    words: int,
    answer: AnswerLength,
    mode: str = "review",
    *,
    text_answer: AnswerLength | None = None,
    overhead_chars: int = 0,
) -> TimeRange:
    """Return how long proofreading a text of *words* words takes.

    The request is the fixed instructions plus the text; the answer is the model's own length
    per 100 words in review mode, and in text mode (corrections only, where the answer is about
    as long as the text) the length real runs measured, or :data:`TEXT_MODE_ANSWER` before
    there are any.

    Args:
        speed: The model's measured rates on this computer.
        words: Words of the student text.
        answer: The model's answer length per 100 words in review mode.
        mode: ``"review"`` or ``"text"``.
        text_answer: The answer length per 100 words measured in text mode; ``None`` takes
            :data:`TEXT_MODE_ANSWER`.  Ignored in review mode.
        overhead_chars: Characters of the fixed part of the request (instructions and answer
            format), when the caller knows them.
    """
    read_s = _prompt_seconds(speed, overhead_chars + words * CHARS_PER_WORD)
    per_100 = (text_answer or TEXT_MODE_ANSWER) if mode == "text" else answer
    return TimeRange(
        low_s=read_s + words / 100 * per_100.low / speed.write_tps,
        high_s=read_s + words / 100 * per_100.high / speed.write_tps,
    )


def content_review_time(speed: ModelSpeed, words: int, *, overhead_chars: int = 0) -> TimeRange:
    """Return how long the content review of a text of *words* words takes.

    The same shape as :func:`proofreading_time`, with :data:`CONTENT_REVIEW_ANSWER`: a review
    answers with a short assessment, so its answer length does not depend on the model.
    """
    read_s = _prompt_seconds(speed, overhead_chars + words * CHARS_PER_WORD)
    return TimeRange(
        low_s=read_s + words / 100 * CONTENT_REVIEW_ANSWER.low / speed.write_tps,
        high_s=read_s + words / 100 * CONTENT_REVIEW_ANSWER.high / speed.write_tps,
    )


def batch_time(
    files: int,
    pages_per_file: int,
    steps: Sequence[TimeRange],
    *,
    swaps: bool = False,
    load_s: Sequence[float] = (),
) -> TimeRange:
    """Return how long a run of *files* files takes.

    Every file pays every step for every page.  When the models do not stay loaded together
    (*swaps*), every file pays each model's load time again: a run does all steps for one file
    before it starts the next, so the models alternate.

    Args:
        files: Number of documents in the run.
        pages_per_file: Pages of one document.
        steps: The time one page takes, per step that runs.
        swaps: Whether the models swap between the steps of one file.
        load_s: The load times (seconds) of the models that swap; ignored without *swaps*.
    """
    per_file = TimeRange(0.0, 0.0)
    for step in steps:
        per_file = per_file + step.scaled(pages_per_file)
    if swaps:
        per_file = per_file + TimeRange(sum(load_s), sum(load_s))
    return per_file.scaled(files)


def band(seconds_per_page: float) -> SpeedBand:
    """Return the band of *seconds_per_page* (the limits are inclusive)."""
    if seconds_per_page <= FAST_LIMIT_S:
        return SpeedBand.FAST
    if seconds_per_page <= OK_LIMIT_S:
        return SpeedBand.OK
    if seconds_per_page <= SLOW_LIMIT_S:
        return SpeedBand.SLOW
    return SpeedBand.VERY_SLOW


def card_not_used(on_card: float | None, gpu_found: bool) -> bool:
    """Return whether Ollama ran the model on the processor although a card was found.

    Only a share of exactly 0 counts: a share between 0 and 1 is a hint at best, because
    Ollama's size figures are wrong for some models.
    """
    return gpu_found and on_card == 0


def default_answer_length(category: ModelCategory) -> AnswerLength:
    """Return the median answer length of the catalogued models of *category*.

    A model without a measured figure gets the middle of what its kind measured, not a constant
    of its own: the catalog is the best evidence there is for a model nobody measured.
    """
    measured = [
        spec.answer_length
        for spec in OLLAMA_CATALOG
        if spec.category == category and spec.answer_length is not None
    ]
    if not measured:
        return DEFAULT_READING_ANSWER if category == "ocr" else CONTENT_REVIEW_ANSWER
    return AnswerLength(
        low=round(median(item.low for item in measured)),
        typical=round(median(item.typical for item in measured)),
        high=round(median(item.high for item in measured)),
    )


def answer_length_for(spec: OllamaModelSpec | None, category: ModelCategory) -> AnswerLength:
    """Return the answer length of *spec*, else the median of its kind."""
    if spec is not None and spec.answer_length is not None:
        return spec.answer_length
    return default_answer_length(category)


def machine_factor(measured_tps: float, reference_tps: float) -> float | None:
    """Return how much slower this computer is than the reference machine, or ``None``.

    ``1.0`` means the same speed; ``0.5`` means half.  ``None`` when either figure is missing,
    so a caller cannot mistake "unknown" for "the same".
    """
    if measured_tps <= 0 or reference_tps <= 0:
        return None
    return measured_tps / reference_tps


def median_factor(factors: Sequence[float]) -> float | None:
    """Return the median of *factors*, or ``None`` when there is none."""
    usable = [factor for factor in factors if factor > 0]
    return median(usable) if usable else None


def percentile(values: Sequence[float], percent: float) -> float:
    """Return the *percent* percentile of the sorted *values*, interpolating between them.

    The ends of a range are one runaway answer away from being useless; the 10th and 90th
    percentile of the same figures are not.  ``0.0`` for no values, so a caller that has none
    gets a figure it can print rather than an exception.
    """
    if not values:
        return 0.0
    position = (len(values) - 1) * percent / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


@dataclass(frozen=True)
class MachineFactors:
    """How much slower this computer is than the reference machine, per rate.

    Two figures, because a card can be better at one than at the other: reading a page image
    and writing an answer are different work.  ``1.0`` means the same speed, ``0.5`` half.
    """

    writing: float | None = None
    """Tokens of an answer per second, against the reference machine."""
    reading: float | None = None
    """Tokens of a request per second, against the reference machine."""

    def for_category(self, category: ModelCategory) -> float | None:
        """Return the factor to use for *category*: the reading one, else the writing one.

        A reading model is measured by how fast it reads a page image, a writing model by how
        fast it writes.  Without a measured reading model the writing factor serves for both:
        it is the same card, and a guess from the wrong rate beats no figure at all.
        """
        if category == "ocr":
            return self.reading if self.reading is not None else self.writing
        return self.writing


def expected_speed(spec: OllamaModelSpec, factor: float | None) -> ModelSpeed | None:
    """Return the speed *spec* is expected to reach here, from its reference speed.

    ``None`` when the model has no reference speed or no factor is known: a model that was never
    measured anywhere gets no guess.
    """
    reference = spec.reference_speed
    if reference is None or factor is None or factor <= 0:
        return None
    read_tps = reference.read_tps if reference.read_tps is not None else reference.write_tps
    return ModelSpeed(
        read_tps=factor * read_tps,
        write_tps=factor * reference.write_tps,
        image_tokens=DEFAULT_IMAGE_TOKENS if spec.category == "ocr" else None,
    )


def expected_band(
    spec: OllamaModelSpec,
    factor: float | None,
    *,
    words: int = TYPICAL_PAGE_WORDS,
) -> SpeedBand | None:
    """Return the band *spec* is expected to reach on this computer, or ``None``.

    The band of the middle of the range: the range itself is three times wide, so its ends
    would name two different bands for the same model.
    """
    speed = expected_speed(spec, factor)
    if speed is None:
        return None
    answer = answer_length_for(spec, spec.category)
    if spec.category == "ocr":
        page = reading_page_time(speed, answer)
    else:
        page = proofreading_time(speed, words, answer)
    return band(page.typical_s)
