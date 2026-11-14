"""Text correction implementations.

:class:`LangChainCorrector` — accepts any LangChain ``BaseChatModel``; provider-agnostic.
:class:`NativeOllamaCorrector` — uses :class:`~.ollama_utils.OllamaClient` directly; no LangChain.

Both implement :class:`~teachers_teammate.interfaces.Corrector` and return a
:class:`~teachers_teammate.domain.correction_review.CorrectionOutcome`, which carries the
corrected text and, in *review* mode, the explanations and the unclear expressions.

Two output modes exist (:data:`~.correction_schema.OUTPUT_MODES`). ``review`` asks for the
structured answer of :mod:`.correction_schema`; ``text`` is the older behaviour and returns the
corrected text only.  When a review answer cannot be produced, the corrector falls back to
``text`` mode for that file and says so in the warning.

The **instructions** (the *policy*, the presets below) say what to correct; the **answer
format** (``domain/answer_format.py``) says how to fill in the reply.  Both can be edited by the
teacher.  The message that carries the text names the text language and the explanation
language, so neither instructions nor answer format contain a language placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel

    from .ollama_utils import OllamaClient

from ..domain.answer_format import resolve_format
from ..domain.context_budget import REVIEW_ANSWER_FACTOR, ContextBudget
from ..domain.correction_policy import CUT_OFF_TRIES, normalize_output_mode
from ..domain.correction_prompts import (  # noqa: F401  # re-exported for callers and tests
    DUTCH_PROMPT,
    ENGLISH_PROMPT,
    FRENCH_PROMPT,
    GERMAN_PROMPT,
    ITALIAN_PROMPT,
    LEGACY_PRESET_PROMPTS,
    PORTUGUESE_PROMPT,
    PRESET_PROMPTS,
    PROMPTS,
    SPANISH_PROMPT,
    is_preset_prompt_text,
    migrate_saved_prompt,
    normalize_prompt_text,
)
from ..domain.correction_review import (
    CorrectionOutcome,
    CorrectionReview,
    SentenceNote,
    UnclearNote,
    splice,
)
from ..domain.formatted_text import TextFormat
from ..domain.model_catalog import DEFAULT_REQUEST_PROFILE, RequestProfile
from ..domain.notation import with_notation_hint
from ..domain.text_chunks import Chunk, split_for_budget
from ..interfaces import Corrector
from .correction_schema import (
    DEFAULT_LEVEL,
    CorrectionAnswer,
    clean_answer,
    fill_placeholders,
    to_review,
    validate_answer,
)
from .ollama_utils import PROOFREADING_NUM_CTX
from .proofreading.port import (
    REVIEW_TIMEOUT_CAP,  # noqa: F401  # re-exported for the tests that pin the timeout
    LangChainPort,
    ModelPort,
    OllamaPort,
    StepRequest,
    _review_timeout,  # noqa: F401  # re-exported for the tests that pin the timeout
)
from .proofreading.runner import StepRunner

FALLBACK_WARNING = (
    "Explanations could not be produced for this file; only the corrections are shown."
)
"""Warning shown when a review answer failed and the file was corrected in text mode."""

_PROVIDER_ERROR = "Correction failed; keeping original text."

_HUMAN_REVIEW = "Text language: {language}\nExplanation language: {explanation}\n\nText:\n\n{text}"
_HUMAN_TEXT = "Text language: {language}\n\nText:\n\n{text}"


def _human_template(mode: str) -> str:
    """Return the LangChain template of the message that carries the text."""
    return _HUMAN_REVIEW if mode == "review" else _HUMAN_TEXT


def _message_values(
    raw_text: str, language: str, explanation_language: str, mode: str
) -> dict[str, str]:
    """Return the template variables of :func:`_human_template`."""
    values = {"text": raw_text, "language": language}
    if mode == "review":
        values["explanation"] = explanation_language or language
    return values


def _human_message(raw_text: str, language: str, explanation_language: str, mode: str) -> str:
    """Return the message that carries the text, filled in (native Ollama: no template)."""
    values = _message_values(raw_text, language, explanation_language, mode)
    template = _human_template(mode)
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


@dataclass(frozen=True)
class CorrectionSettings:
    """Everything a corrector needs besides the model and the client."""

    prompt: str = ""
    level: str = DEFAULT_LEVEL
    explanation_language: str = ""
    output_mode: str = "review"
    answer_format_review: str = ""
    """The teacher's own answer format for explanations ("" = built-in)."""
    answer_format_text: str = ""
    """The teacher's own answer format for corrections only ("" = built-in)."""
    strict_schema: bool = False
    """OpenAI's strict JSON-schema mode; the other providers pick their own mechanism."""
    num_ctx: int = PROOFREADING_NUM_CTX
    """Context window this corrector asks Ollama for, resolved once per run from the hardware."""
    cut_off_tries: int = CUT_OFF_TRIES
    """How often a review request is sent when its answer is cut off (see
    :func:`~teachers_teammate.domain.correction_policy.cut_off_tries`)."""


def _tidy_answer(raw_text: str, text_format: TextFormat) -> Callable[[str], str]:
    """Return the function that brings a proofreading answer into the notation of *raw_text*."""

    def tidy(answer: str) -> str:
        from .ocr_formats import normalize_llm_answer  # noqa: PLC0415

        return normalize_llm_answer(answer, text_format, source=raw_text)

    return tidy


def _plain_answer(text: str) -> tuple[str, str]:
    """Return a plain-text answer as it is: text mode has no schema to validate."""
    return text, ""


# ── Policy presets ────────────────────────────────────────────────────────────
# The preset texts live in ``domain/correction_prompts.py`` (imported above).


def _resolve_prompt(prompt: str, language: str = "") -> str:
    """Return the system prompt: named presets, auto-match by language, or custom string."""
    if prompt:
        return PROMPTS.get(prompt, prompt)
    # Auto-match to a language-specific preset when no custom prompt is set.
    return PROMPTS.get(language.lower(), ENGLISH_PROMPT)


def prompt_ignores_level(prompt: str) -> bool:
    """Whether *prompt* is a custom instruction text that does not use the ``{level}`` setting.

    Empty prompts and preset names resolve to a built-in preset, which always uses the level.
    """
    return bool(prompt.strip()) and prompt not in PROMPTS and "{level}" not in prompt


# ── Request building ──────────────────────────────────────────────────────────


COMPACT_ANSWER_RULE = (
    "Write the JSON compactly on a single line: no indentation and no line breaks."
)
"""What a review request carries when the model's profile asks for it.

A pretty-printed review answer spent 29 % of its tokens on indentation and line breaks, and the
window then cut the answer off for texts of about 3,000 characters.  The rule sits in the fixed
part of the instructions, not in the editable :data:`~.answer_format.REVIEW_FORMAT`, so a
teacher's own format gets it too.

It is **not** sent to every model: the families react differently (some collapse to a single
sentence with it, others ignore it), so
:class:`~teachers_teammate.domain.model_catalog.RequestProfile` decides per model.
"""


def instruction_parts(
    prompt: str,
    language: str,
    *,
    level: str,
    text_format: TextFormat,
    mode: str,
    answer_format: str = "",
    compact_json: bool = False,
) -> tuple[str, str]:
    """Return ``(policy, fixed)``: the teacher's instructions and the answer format.

    *policy* is the resolved preset or the custom prompt with ``{level}`` filled in.
    *fixed* is the answer format of *mode* (the teacher's *answer_format*, else the built-in
    one) plus the notation hint for Markdown texts and, in review mode with *compact_json*, the
    compact-JSON rule.  The dialog shows these parts; :func:`_instructions` sends their join.

    *compact_json* is the model's profile knob
    (:class:`~teachers_teammate.domain.model_catalog.RequestProfile`), so the caller resolves it
    from the model tag: the corrector, the dialog and the cache key all ask
    :func:`~teachers_teammate.domain.model_catalog.request_profile`.
    """
    policy = fill_placeholders(_resolve_prompt(prompt, language), level=level)
    fixed = with_notation_hint(resolve_format(mode, answer_format), text_format, task="correction")
    if mode == "review" and compact_json:
        fixed = f"{fixed}\n{COMPACT_ANSWER_RULE}"
    return policy, fixed


def _instructions(
    prompt: str,
    language: str,
    *,
    level: str,
    text_format: TextFormat,
    mode: str,
    answer_format: str = "",
    compact_json: bool = False,
) -> str:
    """Return the system instructions: policy + answer format + notation hint, unescaped.

    The caller escapes braces when the text becomes a LangChain template; the native Ollama
    path uses the string as it is.
    """
    policy, fixed = instruction_parts(
        prompt,
        language,
        level=level,
        text_format=text_format,
        mode=mode,
        answer_format=answer_format,
        compact_json=compact_json,
    )
    return f"{policy}\n\n{fixed}"


def request_messages(  # noqa: PLR0913  # a request is many independent facts, all keyword-only
    text: str,
    language: str,
    *,
    prompt: str = "",
    level: str = DEFAULT_LEVEL,
    text_format: TextFormat = "plain",
    mode: str = "review",
    answer_format: str = "",
    compact_json: bool = False,
    explanation_language: str = "",
) -> tuple[str, str]:
    """Return ``(system, user)``: the messages a corrector sends for *text*.

    The one public way to build a request, so a tool that measures the app's request (the
    evaluation tools) asks here and cannot drift from what the corrector sends.

    Args:
        text: The text the user message carries.
        language: The language *name* the text is in, as the corrector is asked for it.
        prompt: The teacher's instructions; empty takes the built-in ones for *language*.
        level: The CEFR level the instructions are filled in for.
        text_format: The notation of the text (Markdown adds a hint to the system message).
        mode: ``review`` or ``text``.
        answer_format: The teacher's own answer format for *mode*; empty takes the built-in one.
        compact_json: Whether the model's request profile adds the compact-JSON rule.
        explanation_language: The language of the explanations; empty takes *language*.
    """
    system = _instructions(
        prompt,
        language,
        level=level,
        text_format=text_format,
        mode=mode,
        answer_format=answer_format,
        compact_json=compact_json,
    )
    return system, _human_message(text, language, explanation_language, mode)


def _review_outcome(
    answer: CorrectionAnswer, source: str, text_format: TextFormat
) -> CorrectionOutcome | None:
    """Splice *answer* into *source*; return ``None`` when not a single sentence could be placed.

    The answer is cleaned first (:func:`~.correction_schema.clean_answer`): markup the model
    added to a plain text, a ``corrected`` that is not the sentence, and a change that changes
    nothing would otherwise reach the teacher's text through the splice.
    """
    answer, _repairs = clean_answer(answer, text_format)
    review = to_review(answer)
    corrected, unplaced = splice(source, review.sentences)
    if review.sentences and len(unplaced) == len(review.sentences):
        return None
    review = replace(review, unplaced=unplaced)
    warning = None
    if unplaced:
        count = len(unplaced)
        noun = "suggestion" if count == 1 else "suggestions"
        warning = f"{count} {noun} could not be placed in the text."
    return CorrectionOutcome(text=corrected, review=review, warning=warning)


def _with_fallback_warning(text: str, warning: str | None) -> CorrectionOutcome:
    """Return a text-mode outcome that carries the fallback warning as well.

    A *warning* from the text-mode call itself means that call failed too.
    """
    merged = FALLBACK_WARNING if warning is None else f"{FALLBACK_WARNING} {warning}"
    return CorrectionOutcome(
        text=text, review=None, warning=merged, failed=warning is not None, fell_back=True
    )


class _ReviewCorrector(Corrector):
    """Shared review/text-mode plumbing; subclasses supply the connection (a port)."""

    _level: str
    _explanation_language: str
    _output_mode: str
    _prompt: str
    _answer_formats: dict[str, str]
    _num_ctx: int
    _profile: RequestProfile
    _cut_off_tries: int
    _temperature: float
    _port: ModelPort

    def _configure(self, prompt: str, settings: CorrectionSettings | None) -> None:
        """Store the prompt and apply *settings* (the defaults when none are given)."""
        cfg = settings or CorrectionSettings()
        self._prompt = prompt or cfg.prompt
        self._level = cfg.level
        self._explanation_language = cfg.explanation_language
        self._output_mode = normalize_output_mode(cfg.output_mode)
        self._answer_formats = {
            "review": cfg.answer_format_review,
            "text": cfg.answer_format_text,
        }
        self._num_ctx = cfg.num_ctx
        self._cut_off_tries = max(1, cfg.cut_off_tries)
        self._profile = DEFAULT_REQUEST_PROFILE

    #: Mode actually used: ``review`` unless corrections-only was chosen or is unsupported.
    @property
    def output_mode(self) -> str:
        """Return the mode this corrector uses for its requests."""
        return self._output_mode

    @property
    def profile(self) -> RequestProfile:
        """Return how this corrector asks its model to proofread."""
        return self._profile

    def _messages(
        self, raw_text: str, language: str, text_format: TextFormat, *, mode: str
    ) -> tuple[str, str]:
        """Return ``(system, user)``: the messages a request of *mode* carries."""
        return request_messages(
            raw_text,
            language,
            prompt=self._prompt,
            level=self._level,
            text_format=text_format,
            mode=mode,
            answer_format=self._answer_formats.get(mode, ""),
            compact_json=self._profile.compact_json,
            explanation_language=self._explanation_language,
        )

    def _request(
        self, raw_text: str, language: str, text_format: TextFormat, *, mode: str
    ) -> StepRequest:
        """Return the request of *mode* for *raw_text*."""
        system, user = self._messages(raw_text, language, text_format, mode=mode)
        review = mode == "review"
        return StepRequest(
            step="review" if review else "correct",
            system=system,
            user=user,
            schema=CorrectionAnswer if review else None,
            temperature=self._temperature,
            think=self._profile.think,
            answer_factor=REVIEW_ANSWER_FACTOR if review else 1.0,
        )

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the proofread text, its review (review mode only) and an optional warning."""
        if self.output_mode == "review":
            outcome = self._correct_review(raw_text, language, text_format)
            if outcome is not None:
                return outcome
            text, warning = self._correct_text(raw_text, language, text_format)
            return _with_fallback_warning(text, warning)
        text, warning = self._correct_text(raw_text, language, text_format)
        return CorrectionOutcome(
            text=text, review=None, warning=warning, failed=warning is not None
        )

    def _correct_text(
        self, raw_text: str, language: str, text_format: TextFormat
    ) -> tuple[str, str | None]:
        """Correct in text mode: corrected text and an optional warning, never a review."""
        outcome = StepRunner(self._port).run(
            self._request(raw_text, language, text_format, mode="text"), _plain_answer
        )
        if outcome.value is None:
            return raw_text, outcome.error or _PROVIDER_ERROR
        return _tidy_answer(raw_text, text_format)(outcome.value), None

    def _correct_review(
        self, raw_text: str, language: str, text_format: TextFormat
    ) -> CorrectionOutcome | None:
        """Correct in review mode; ``None`` means the answer was unusable (-> text mode).

        The rules of the requests themselves live in
        :class:`~teachers_teammate.infrastructure.proofreading.runner.StepRunner`: a cut-off
        answer is asked again, an answer that does not match the schema gets one repair request,
        a repair that cannot fit the window is skipped, and a transport error ends the step.

        What is left here is what a failure means for the file.  A cut-off or unusable review
        answer does not fail the file: text mode answers with about the text's length and has the
        shorter system prompt, so it often fits where the review did not, and the caller falls
        back to it.  A provider error (``transport``) keeps the source text and reports the
        error: text mode would meet the same unreachable server.
        """
        outcome = StepRunner(self._port, cut_off_tries=self._cut_off_tries).run(
            self._request(raw_text, language, text_format, mode="review"), validate_answer
        )
        if outcome.failure == "transport":
            return CorrectionOutcome(
                text=raw_text, review=None, warning=outcome.error or _PROVIDER_ERROR, failed=True
            )
        if outcome.value is None:
            return None
        return _review_outcome(outcome.value, raw_text, text_format)


class LangChainCorrector(_ReviewCorrector):
    """LangChain-backed proofreading service (grammar & spelling correction).

    Accepts any :class:`~langchain_core.language_models.BaseChatModel`, making
    the underlying LLM trivially swappable.  Build the model first with
    :func:`~teachers_teammate.infrastructure.llm_factory.build_llm`.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        prompt: str = "",
        *,
        settings: CorrectionSettings | None = None,
        profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    ) -> None:
        self._configure(prompt, settings)
        self._profile = profile
        self._temperature = 0.0  # the model carries its own temperature
        self._port = LangChainPort(llm, strict=(settings or CorrectionSettings()).strict_schema)


class NativeOllamaCorrector(_ReviewCorrector):
    """Ollama-backed proofreading using the native HTTP API (no LangChain).

    Accepts an :class:`~.ollama_utils.OllamaClient` injected by the stage
    builder — the client is shared across pipeline stages and must not be
    constructed here.
    """

    def __init__(
        self,
        model: str,
        client: OllamaClient,
        prompt: str = "",
        timeout: float = 120.0,
        temperature: float = 0.7,
        *,
        settings: CorrectionSettings | None = None,
        profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    ) -> None:
        self._configure(prompt, settings)
        self._profile = profile
        self._temperature = temperature
        self._port = OllamaPort(client, model, timeout=timeout, num_ctx=self._num_ctx)

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int:
        """Return how many characters of text one review request can carry.

        Computed from this corrector's own system prompt and the fixed part of the message that
        carries the text, so the figure cannot drift from what is really sent.  Review mode uses
        the review prompt, which is longer than the text-mode one, and its answer is longer than
        the text (:data:`~teachers_teammate.domain.context_budget.REVIEW_ANSWER_FACTOR`), so the
        text takes a smaller share of the window than it would in text mode.
        """
        system, user = self._messages("", language, text_format, mode="review")
        overhead = len(system) + len(user)
        return ContextBudget(self._num_ctx).max_text_chars(
            overhead, answer_factor=REVIEW_ANSWER_FACTOR
        )


# ── Chunked proofreading ──────────────────────────────────────────────────────

CHUNK_FAILURE = "Proofreading part of the text failed; keeping the original text."
"""Warning of a chunked file whose one piece failed: no half-corrected text is ever stored."""

MISSING_REVIEW_WARNING = "Explanations are missing for part of the text."
"""Warning when only some pieces of a long text got a review answer (the rest fell back)."""


class ChunkingCorrector(Corrector):
    """Proofreads a text that is too long for one request in several pieces.

    Wraps a corrector whose provider can only carry a limited amount of text per request.  The
    wrapped corrector says how much that is (:meth:`Corrector.max_text_chars`), asked for each
    call because the budget depends on the notation: Markdown adds a notation hint to the
    system prompt.  A text that fits — and every text when the budget is not positive — goes to
    the wrapped corrector unchanged, so the common case behaves exactly as before.  A longer
    text is split by :func:`~teachers_teammate.domain.text_chunks.split_for_budget` at
    paragraph, then sentence boundaries; each piece is corrected on its own and the answers are
    merged back into one text, with the original whitespace between the pieces.

    Splitting costs the context across a boundary: a change that depends on a sentence before
    the cut cannot be made.  Cutting at paragraph and sentence ends keeps that rare.

    Failure is all-or-nothing: one failed piece fails the file and keeps the original text,
    because a half-corrected text must never be stored.  The pieces after a failed one are not
    sent: their answers would be thrown away, and with an unreachable server each would wait
    out the timeout.  A piece that fell back to text mode only costs the explanations of that
    piece; the file is not failed and says so in the warning.
    """

    def __init__(self, inner: Corrector) -> None:
        self._inner = inner

    @property
    def inner(self) -> Corrector:
        """Return the corrector that handles one piece."""
        return self._inner

    def split(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> tuple[Chunk, ...] | None:
        """Return the pieces *raw_text* is proofread in, or ``None`` when it goes out whole.

        A text goes out whole when the wrapped corrector has no budget, or the text fits it.
        The one place that decides, so :meth:`correct` and a tool that counts the requests of a
        text cannot disagree.
        """
        max_chars = self._inner.max_text_chars(language, text_format=text_format)
        if max_chars is None or max_chars <= 0 or len(raw_text) <= max_chars:
            return None
        return tuple(split_for_budget(raw_text, max_chars, text_format))

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the proofread text, merging the reviews of all the pieces."""
        chunks = self.split(raw_text, language, text_format=text_format)
        if chunks is None:
            return self._inner.correct(raw_text, language, text_format=text_format)
        outcomes: list[CorrectionOutcome] = []
        for chunk in chunks:
            if not chunk.text.strip():
                # Whitespace-only pieces carry nothing to proofread; a request would be wasted.
                outcomes.append(CorrectionOutcome(text=chunk.text))
                continue
            outcome = self._inner.correct(chunk.text, language, text_format=text_format)
            if outcome.failed:
                return CorrectionOutcome(
                    text=raw_text,
                    review=None,
                    warning=outcome.warning or CHUNK_FAILURE,
                    failed=True,
                )
            outcomes.append(outcome)
        answerable = [
            outcome for outcome, chunk in zip(outcomes, chunks, strict=True) if chunk.text.strip()
        ]
        reviews = [outcome.review for outcome in answerable if outcome.review is not None]
        incomplete = bool(reviews) and len(reviews) < len(answerable)
        warning = _join_warnings(
            *(outcome.warning for outcome in outcomes),
            MISSING_REVIEW_WARNING if incomplete else None,
        )
        return CorrectionOutcome(
            text=_join_text(outcomes, chunks),
            review=_merged_review(reviews) if reviews else None,
            warning=warning,
            fell_back=any(outcome.fell_back for outcome in outcomes),
        )


def proofreading_pieces(
    corrector: Corrector, text: str, language: str, *, text_format: TextFormat = "plain"
) -> tuple[int | None, int | None]:
    """Return how many requests proofreading *text* takes, and the characters one carries.

    ``(None, None)`` for a corrector that sends any length in one request (an online
    provider).  The count is the number of non-blank pieces, because
    :meth:`ChunkingCorrector.correct` sends no request for a whitespace-only piece, and 1 when
    the text goes out whole.  The one place that counts, so the GUI counter, the console line
    and the run cannot disagree.
    """
    if not isinstance(corrector, ChunkingCorrector):
        return None, None
    max_chars = corrector.inner.max_text_chars(language, text_format=text_format)
    if max_chars is None:
        return None, None
    chunks = corrector.split(text, language, text_format=text_format)
    if chunks is None:
        return 1, max_chars
    return max(1, sum(1 for chunk in chunks if chunk.text.strip())), max_chars


def _join_warnings(*warnings: str | None) -> str | None:
    """Return the distinct warnings joined by a space, or ``None`` when there is none."""
    seen = tuple(dict.fromkeys(warning for warning in warnings if warning))
    return " ".join(seen) or None


def _join_text(outcomes: list[CorrectionOutcome], chunks: tuple[Chunk, ...]) -> str:
    """Return the corrected pieces joined with the separators of the source text."""
    return "".join(
        outcome.text + chunk.separator for outcome, chunk in zip(outcomes, chunks, strict=True)
    )


def _merged_review(reviews: list[CorrectionReview]) -> CorrectionReview:
    """Return one review over the whole text from the reviews of its pieces."""
    sentences: list[SentenceNote] = []
    unclear: list[UnclearNote] = []
    unplaced: list[int] = []
    for review in reviews:
        offset = len(sentences)
        sentences.extend(review.sentences)
        unclear.extend(review.unclear)
        unplaced.extend(index + offset for index in review.unplaced)
    return CorrectionReview(
        sentences=tuple(sentences), unclear=tuple(unclear), unplaced=tuple(unplaced)
    )
