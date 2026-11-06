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
from ..domain.correction_policy import normalize_output_mode
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
from ..domain.correction_review import CorrectionOutcome, splice
from ..domain.formatted_text import TextFormat
from ..domain.notation import with_notation_hint
from ..interfaces import Corrector
from ._llm_invoke import (
    _CORRECTION_FAILURE,
    StructuredResult,
    invoke_langchain_chain,
    invoke_langchain_structured,
    invoke_ollama_chat,
    invoke_ollama_structured,
    tidy_answer,
)
from .correction_schema import (
    DEFAULT_LEVEL,
    CorrectionAnswer,
    escape_braces,
    fill_placeholders,
    repair_message,
    schema_json,
    to_review,
    validate_answer,
)

FALLBACK_WARNING = (
    "Explanations could not be produced for this file; only the corrections are shown."
)
"""Warning shown when a review answer failed and the file was corrected in text mode."""

_PROVIDER_ERROR = "Correction failed; keeping original text."

REVIEW_TIMEOUT_CAP = 600.0
"""Upper bound in seconds for a review request, whatever the text length is."""

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


def _tidy_answer(raw_text: str, text_format: TextFormat) -> Callable[[str], str]:
    """Return the function that brings a proofreading answer into the notation of *raw_text*."""

    def tidy(answer: str) -> str:
        from .ocr_formats import normalize_llm_answer  # noqa: PLC0415

        return normalize_llm_answer(answer, text_format, source=raw_text)

    return tidy


def _review_timeout(base_timeout: float, raw_text: str) -> float:
    """Return the timeout of a review request: longer answers need longer than the OCR timeout.

    The cap bounds the growth with the text, never a timeout the user configured themselves.
    """
    return max(base_timeout, min(REVIEW_TIMEOUT_CAP, 60.0 + 0.05 * len(raw_text)))


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


def instruction_parts(
    prompt: str,
    language: str,
    *,
    level: str,
    text_format: TextFormat,
    mode: str,
    answer_format: str = "",
) -> tuple[str, str]:
    """Return ``(policy, fixed)``: the teacher's instructions and the answer format.

    *policy* is the resolved preset or the custom prompt with ``{level}`` filled in.
    *fixed* is the answer format of *mode* (the teacher's *answer_format*, else the built-in
    one) plus the notation hint for Markdown texts.  The dialog shows these parts;
    :func:`_instructions` sends their join.
    """
    policy = fill_placeholders(_resolve_prompt(prompt, language), level=level)
    fixed = with_notation_hint(resolve_format(mode, answer_format), text_format, task="correction")
    return policy, fixed


def _instructions(
    prompt: str,
    language: str,
    *,
    level: str,
    text_format: TextFormat,
    mode: str,
    answer_format: str = "",
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
    )
    return f"{policy}\n\n{fixed}"


def _review_outcome(answer: CorrectionAnswer, source: str) -> CorrectionOutcome | None:
    """Splice *answer* into *source*; return ``None`` when not a single sentence could be placed."""
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
    return CorrectionOutcome(text=text, review=None, warning=merged, failed=warning is not None)


class _ReviewCorrector(Corrector):
    """Shared review/text-mode plumbing; subclasses supply the two provider calls."""

    _level: str
    _explanation_language: str
    _output_mode: str
    _prompt: str
    _answer_formats: dict[str, str]

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

    #: Mode actually used: ``review`` unless corrections-only was chosen or is unsupported.
    @property
    def output_mode(self) -> str:
        """Return the mode this corrector uses for its requests."""
        return self._output_mode

    def _system(self, language: str, text_format: TextFormat, *, mode: str) -> str:
        """Return the unescaped system instructions for *mode*."""
        return _instructions(
            self._prompt,
            language,
            level=self._level,
            text_format=text_format,
            mode=mode,
            answer_format=self._answer_formats.get(mode, ""),
        )

    def _message_values(self, raw_text: str, language: str, mode: str) -> dict[str, str]:
        """Return the variables of the message that carries the text."""
        return _message_values(raw_text, language, self._explanation_language, mode)

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
        raise NotImplementedError

    def _correct_review(
        self, raw_text: str, language: str, text_format: TextFormat
    ) -> CorrectionOutcome | None:
        """Correct in review mode; ``None`` means the answer was unusable (-> text mode).

        A provider error keeps the source text and reports the error.  An answer that does not
        match the schema gets one repair request quoting the validation error; a provider that
        cannot produce a schema-bound answer, an empty answer, or one that is still invalid
        after the repair all return ``None`` so the caller corrects in text mode.
        """
        result = self._ask(raw_text, language, text_format, repair=None)
        if result.failure == "invalid" and result.raw.strip():
            result = self._ask(raw_text, language, text_format, repair=(result.raw, result.error))
        if result.failure == "transport":
            return CorrectionOutcome(
                text=raw_text, review=None, warning=result.error or _PROVIDER_ERROR, failed=True
            )
        if result.parsed is None:
            return None
        return _review_outcome(result.parsed, raw_text)

    def _ask(
        self,
        raw_text: str,
        language: str,
        text_format: TextFormat,
        *,
        repair: tuple[str, str] | None,
    ) -> StructuredResult[CorrectionAnswer]:
        """Ask the model for a schema-bound answer; *repair* is ``(previous answer, error)``."""
        raise NotImplementedError


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
    ) -> None:
        self._llm = llm
        self._configure(prompt, settings)
        self._strict_schema = (settings or CorrectionSettings()).strict_schema

    def _correct_text(
        self, raw_text: str, language: str, text_format: TextFormat
    ) -> tuple[str, str | None]:
        result = invoke_langchain_chain(
            self._llm,
            system=escape_braces(self._system(language, text_format, mode="text")),
            human=_human_template("text"),
            variables=self._message_values(raw_text, language, "text"),
            fallback=raw_text,
            failure_prefix="Correction failed",
            failure_suffix="; keeping original text.",
        )
        return tidy_answer(result, _tidy_answer(raw_text, text_format))

    def _ask(
        self,
        raw_text: str,
        language: str,
        text_format: TextFormat,
        *,
        repair: tuple[str, str] | None,
    ) -> StructuredResult[CorrectionAnswer]:
        human = _human_template("review")
        if repair is not None:
            human = f"{human}\n\n{escape_braces(repair_message(*repair))}"
        return invoke_langchain_structured(
            self._llm,
            system=escape_braces(self._system(language, text_format, mode="review")),
            human=human,
            variables=self._message_values(raw_text, language, "review"),
            schema=CorrectionAnswer,
            strict=self._strict_schema,
        )


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
    ) -> None:
        self._model = model
        self._client = client
        self._timeout = timeout
        self._temperature = temperature
        self._configure(prompt, settings)

    def _messages(
        self, raw_text: str, language: str, text_format: TextFormat, *, mode: str
    ) -> tuple[str, str]:
        """Return ``(system, user)`` for the native API (no template, no escaping)."""
        return (
            self._system(language, text_format, mode=mode),
            _human_message(raw_text, language, self._explanation_language, mode),
        )

    def _correct_text(
        self, raw_text: str, language: str, text_format: TextFormat
    ) -> tuple[str, str | None]:
        system, user = self._messages(raw_text, language, text_format, mode="text")
        result = invoke_ollama_chat(
            self._client,
            model=self._model,
            prompt=user,
            system=system,
            timeout=self._timeout,
            temperature=self._temperature,
            fallback=raw_text,
            failure=_CORRECTION_FAILURE,
        )
        return tidy_answer(result, _tidy_answer(raw_text, text_format))

    def _ask(
        self,
        raw_text: str,
        language: str,
        text_format: TextFormat,
        *,
        repair: tuple[str, str] | None,
    ) -> StructuredResult[CorrectionAnswer]:
        system, prompt = self._messages(raw_text, language, text_format, mode="review")
        if repair is not None:
            prompt = f"{prompt}\n\n{repair_message(*repair)}"
        return invoke_ollama_structured(
            self._client,
            model=self._model,
            prompt=prompt,
            system=system,
            schema=schema_json(),
            timeout=_review_timeout(self._timeout, raw_text),
            temperature=self._temperature,
            parse=validate_answer,
        )
