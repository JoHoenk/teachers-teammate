"""Unit tests for teachers_teammate.correction."""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from teachers_teammate.domain.answer_format import REVIEW_FORMAT, TEXT_FORMAT
from teachers_teammate.exceptions import TRUNCATED_MESSAGE
from teachers_teammate.infrastructure import correction as correction_module
from teachers_teammate.infrastructure._llm_invoke import StructuredResult
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.infrastructure.correction import (
    COMPACT_ANSWER_RULE,
    ENGLISH_PROMPT,
    FALLBACK_WARNING,
    GERMAN_PROMPT,
    REVIEW_TIMEOUT_CAP,
    CorrectionSettings,
    LangChainCorrector,
    NativeOllamaCorrector,
    prompt_ignores_level,
    _resolve_prompt,
    _review_timeout,
)
from teachers_teammate.infrastructure.correction_schema import (
    CorrectionAnswer,
    fill_placeholders,
    schema_json,
)

_TEXT_ONLY = CorrectionSettings(output_mode="text")
"""Settings that ask for the corrected text only, the pre-review behaviour."""

# ── _resolve_prompt ────────────────────────────────────────────────────────


def test_resolve_prompt_empty_with_english_language() -> None:
    """
    Given  an empty prompt string and language="english"
    When   _resolve_prompt is called
    Then   the built-in ENGLISH_PROMPT is returned
    """
    assert _resolve_prompt("", "english") == ENGLISH_PROMPT


def test_resolve_prompt_empty_language_falls_back_to_english() -> None:
    """
    Given  an empty prompt string and an empty language string
    When   _resolve_prompt is called
    Then   the built-in ENGLISH_PROMPT is used as the default
    """
    assert _resolve_prompt("", "") == ENGLISH_PROMPT


def test_resolve_prompt_german_by_language() -> None:
    """
    Given  an empty prompt string and language="german"
    When   _resolve_prompt is called
    Then   the built-in GERMAN_PROMPT is returned
    """
    assert _resolve_prompt("", "german") == GERMAN_PROMPT


def test_resolve_prompt_german_by_preset_name() -> None:
    """
    Given  a prompt string set to the preset name "german" with any language
    When   _resolve_prompt is called
    Then   the built-in GERMAN_PROMPT is returned (preset name takes precedence)
    """
    assert _resolve_prompt("german", "") == GERMAN_PROMPT


def test_resolve_prompt_custom_string_returned_verbatim() -> None:
    """
    Given  a prompt string that is not a known preset name
    When   _resolve_prompt is called
    Then   the custom prompt string is returned unchanged
    """
    custom = "Do only minimal corrections. Return only corrected text."
    assert _resolve_prompt(custom, "english") == custom


def test_resolve_prompt_case_insensitive_language() -> None:
    """
    Given  a language string with varying capitalisation ("German" / "GERMAN")
    When   _resolve_prompt is called with an empty prompt
    Then   the language match is case-insensitive and returns GERMAN_PROMPT
    """
    assert _resolve_prompt("", "German") == GERMAN_PROMPT
    assert _resolve_prompt("", "GERMAN") == GERMAN_PROMPT


# ── LangChainCorrector.correct: text mode ──────────────────────────────────


def test_correct_returns_llm_response() -> None:
    """
    Given  a LangChainCorrector in text mode backed by a FakeListChatModel
    When   correct() is called with raw OCR text
    Then   the pre-canned answer is the corrected text, without review or warning
    """
    llm = FakeListChatModel(responses=["Hello world."])
    outcome = LangChainCorrector(llm, settings=_TEXT_ONLY).correct("Helo wrold.", "English")

    assert outcome.text == "Hello world."
    assert outcome.review is None
    assert outcome.warning is None


def test_correct_returns_original_on_exception() -> None:
    """
    Given  a LangChainCorrector in text mode whose model raises
    When   correct() is called
    Then   the original raw text is returned with a warning message
    """
    # FakeListChatModel raises IndexError when its responses list is exhausted.
    llm = FakeListChatModel(responses=[])
    outcome = LangChainCorrector(llm, settings=_TEXT_ONLY).correct("some text", "English")

    assert outcome.text == "some text"
    assert outcome.warning is not None
    assert "Correction failed" in outcome.warning
    assert outcome.review is None


def test_correct_returns_string_for_multiple_calls() -> None:
    """
    Given  a LangChainCorrector in text mode with two pre-canned answers
    When   correct() is called twice
    Then   each call returns the next answer in order
    """
    llm = FakeListChatModel(responses=["First.", "Second."])
    corrector = LangChainCorrector(llm, settings=_TEXT_ONLY)

    assert corrector.correct("first input", "English").text == "First."
    assert corrector.correct("second input", "English").text == "Second."


def test_correct_preset_name_at_init_resolves_correctly() -> None:
    """
    Given  a LangChainCorrector initialised with prompt="german"
    When   the stored _prompt is passed back through _resolve_prompt
    Then   it resolves to GERMAN_PROMPT regardless of the call-time language
    """
    llm = FakeListChatModel(responses=["OK"])
    corrector = LangChainCorrector(llm, prompt="german")
    assert _resolve_prompt(corrector._prompt, "English") == GERMAN_PROMPT


# ── NativeOllamaCorrector ──────────────────────────────────────────────────


def _ollama_client():
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    return OllamaClient("http://localhost:11434")


def test_native_ollama_corrector_passes_temperature_in_options(mocker) -> None:
    """
    Given  a NativeOllamaCorrector in text mode constructed with temperature=0.3
    When   correct() is called
    Then   OllamaClient.chat receives options={"temperature": 0.3} and no format
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="fixed text")
    corrector = NativeOllamaCorrector(
        model="m", client=_ollama_client(), temperature=0.3, settings=_TEXT_ONLY
    )

    outcome = corrector.correct("raw", "English")

    assert outcome.text == "fixed text"
    assert outcome.warning is None
    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.3
    assert call_kwargs["format"] is None


def test_native_ollama_corrector_default_temperature(mocker) -> None:
    """
    Given  a NativeOllamaCorrector in text mode constructed without an explicit temperature
    When   correct() is called
    Then   OllamaClient.chat receives options={"temperature": 0.7}
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client(), settings=_TEXT_ONLY)

    corrector.correct("text", "English")

    assert mock_chat.call_args.kwargs["options"]["temperature"] == 0.7


# ── Markdown notation hint ─────────────────────────────────────────────────


def _captured_ollama_prompt(mocker, text_format: str | None, **settings: object) -> str:
    """Return the whole prompt (system message, then user message) the native path sent."""
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    corrector = NativeOllamaCorrector(
        model="m",
        client=_ollama_client(),
        settings=CorrectionSettings(output_mode="text", **settings),  # ty: ignore[invalid-argument-type]  # parametrised settings
    )
    if text_format is None:
        corrector.correct("~~a~~ b", "English")
    else:
        corrector.correct("~~a~~ b", "English", text_format=text_format)  # ty: ignore[invalid-argument-type]  # parametrised literal
    return f"{mock_chat.call_args.kwargs['system']}\n\n{mock_chat.call_args.args[1]}"


def test_native_ollama_corrector_explains_markdown_notation(mocker) -> None:
    """
    Given  a NativeOllamaCorrector and text flagged as Markdown
    When   correct(text_format="markdown") is called
    Then   the prompt tells the model what ~~strikethrough~~ and <sup> mean and to keep them
    """
    prompt = _captured_ollama_prompt(mocker, "markdown")

    assert "~~text~~ marks words the writer crossed out" in prompt
    assert "<sup>text</sup>" in prompt
    assert prompt.startswith(fill_placeholders(ENGLISH_PROMPT, level="B2"))


def test_native_ollama_corrector_prompt_unchanged_for_plain_text(mocker) -> None:
    """
    Given  a NativeOllamaCorrector and plain text (explicit or default format)
    When   correct() is called
    Then   no notation hint is added, so plain-text prompts carry no Markdown wording
    """
    explicit = _captured_ollama_prompt(mocker, "plain")
    default = _captured_ollama_prompt(mocker, None)

    assert explicit == default
    assert "Markdown" not in explicit


def test_native_ollama_corrector_fills_the_level_into_the_policy(mocker) -> None:
    """
    Given  a NativeOllamaCorrector configured for level C1
    When   correct() is called
    Then   the prompt names the C1 level and no "{level}" placeholder is left
    """
    prompt = _captured_ollama_prompt(mocker, None, level="C1")

    assert "at a C1 level" in prompt
    assert "{level}" not in prompt


def test_langchain_corrector_adds_notation_hint_to_system_message(monkeypatch) -> None:
    """
    Given  a LangChainCorrector and Markdown text
    When   correct(text_format="markdown") is called
    Then   the system message sent to the template contains the notation hint
    """
    captured: list = []

    class _Chain:
        def __or__(self, _other):
            return self

        def invoke(self, payload):
            return payload["text"]

    def fake_from_messages(messages):
        captured.extend(messages)
        return _Chain()

    monkeypatch.setattr(
        "langchain_core.prompts.ChatPromptTemplate.from_messages", fake_from_messages
    )
    monkeypatch.setattr("langchain_core.output_parsers.StrOutputParser", lambda: object())

    LangChainCorrector(object(), settings=_TEXT_ONLY).correct(  # ty: ignore[invalid-argument-type]  # stub llm
        "x", "English", text_format="markdown"
    )

    system = next(content for role, content in captured if role == "system")
    assert system.startswith(fill_placeholders(ENGLISH_PROMPT, level="B2"))
    assert "crossed out" in system


def test_custom_prompt_with_braces_does_not_crash(monkeypatch) -> None:
    """
    Given  a custom prompt that contains stray braces next to the known {level} placeholder
    When   correct() is called in text mode through the LangChain path
    Then   the known placeholder is filled, the other braces are escaped, and nothing raises
    """
    captured: list = []

    class _Chain:
        def __or__(self, _other):
            return self

        def invoke(self, payload):
            return payload["text"]

    def fake_from_messages(messages):
        captured.extend(messages)
        return _Chain()

    monkeypatch.setattr(
        "langchain_core.prompts.ChatPromptTemplate.from_messages", fake_from_messages
    )
    monkeypatch.setattr("langchain_core.output_parsers.StrOutputParser", lambda: object())

    corrector = LangChainCorrector(
        object(),  # ty: ignore[invalid-argument-type]  # stub llm
        prompt="Fix {this} at {level}. Keep {unknown}.",
        settings=_TEXT_ONLY,
    )
    outcome = corrector.correct("x", "English")

    system = next(content for role, content in captured if role == "system")
    # The known placeholder is filled; every other brace is escaped for the template.
    assert "Fix {{this}} at B2. Keep {{unknown}}." in system
    assert outcome.warning is None


# ── Contract, policy and review mode ──────────────────────────────────────


def _answer_json() -> str:
    """Return a valid review answer for the text "Helo wrold."."""
    return json.dumps(
        {
            "sentences": [
                {
                    "original": "Helo wrold.",
                    "corrected": "Hello world.",
                    "changes": [
                        {
                            "original": "Helo",
                            "corrected": "Hello",
                            "category": "spelling",
                            "explanation": "Misspelt word.",
                        }
                    ],
                }
            ],
            "unclear": [],
        }
    )


def test_text_mode_prompt_asks_for_the_corrected_text_only(mocker) -> None:
    """
    Given  a corrector in text mode
    When   a request is built
    Then   the answer format is the corrections-only one, not the JSON one, and it
           tells the model to keep paragraphs and not to add, remove or reorder sentences
    """
    prompt = _captured_ollama_prompt(mocker, None)

    assert TEXT_FORMAT in prompt
    assert "Return only the corrected text, nothing else." in prompt
    assert "Keep the line breaks, paragraphs and formatting" in prompt
    assert "not reorder, add or remove any sentence" in prompt
    assert REVIEW_FORMAT not in prompt


def _review_request(monkeypatch, profile=None, **settings: object) -> dict:
    """Return the keyword arguments of the single review request the corrector sends."""
    from teachers_teammate.domain.model_catalog import DEFAULT_REQUEST_PROFILE  # noqa: PLC0415

    captured: list[dict] = []

    def fake(_llm, **kwargs):
        captured.append(kwargs)
        return StructuredResult(error="(stop)", failure="transport")  # provider error, no repair

    monkeypatch.setattr(correction_module, "invoke_langchain_structured", fake)
    LangChainCorrector(
        FakeListChatModel(responses=[]),
        settings=CorrectionSettings(**settings),  # ty: ignore[invalid-argument-type]  # parametrised settings
        profile=profile or DEFAULT_REQUEST_PROFILE,
    ).correct("x", "German")
    return captured[0]


def _review_system(monkeypatch, **settings: object) -> str:
    """Return the system message of the single review request the corrector sends."""
    return _review_request(monkeypatch, **settings)["system"]


def test_review_prompt_contains_the_answer_format(monkeypatch) -> None:
    """
    Given  a corrector in review mode whose model's profile does not ask for compact JSON
    When   a request is built
    Then   the system message is the policy followed by the review answer format, without the
           compact-JSON rule, and not the corrections-only format
    """
    system = _review_system(monkeypatch)

    assert system == f"{fill_placeholders(GERMAN_PROMPT, level='B2')}\n\n{REVIEW_FORMAT}"
    assert COMPACT_ANSWER_RULE not in system
    assert TEXT_FORMAT not in system


def test_the_compact_rule_follows_the_models_profile(monkeypatch) -> None:
    """
    Given  two correctors that differ only in their model's request profile
    When   each builds its review request
    Then   the rule ends the fixed part when the profile asks for it, and is absent otherwise
    """
    from teachers_teammate.domain.model_catalog import RequestProfile  # noqa: PLC0415

    on = _review_system(monkeypatch, profile=RequestProfile(compact_json=True))
    off = _review_system(monkeypatch, profile=RequestProfile(compact_json=False))

    assert on.endswith(f"{REVIEW_FORMAT}\n{COMPACT_ANSWER_RULE}")
    assert COMPACT_ANSWER_RULE not in off


def test_review_message_names_both_languages(monkeypatch) -> None:
    """
    Given  a review request for a German text without an explanation language
    When   the request is built
    Then   the message carries the text language and, as the default, the same explanation
           language, so the answer format itself needs no language
    """
    request = _review_request(monkeypatch)

    assert "Text language: {language}" in request["human"]
    assert "Explanation language: {explanation}" in request["human"]
    assert request["variables"]["language"] == "German"
    assert request["variables"]["explanation"] == "German"


def test_review_message_uses_the_explanation_language_setting(monkeypatch) -> None:
    """
    Given  an explanation language setting of "English" for a German text
    When   the request is built
    Then   the message names German as the text language and English for the explanations
    """
    request = _review_request(monkeypatch, explanation_language="English")

    assert request["variables"]["language"] == "German"
    assert request["variables"]["explanation"] == "English"


def test_review_prompt_never_translates_the_text(monkeypatch) -> None:
    """
    Given  a German text with English explanations requested
    When   the review request is built
    Then   the answer format forbids translating the student's text
    """
    system = _review_system(monkeypatch, explanation_language="English")

    assert "never translate the student's text" in system


def test_custom_answer_format_replaces_the_built_in_one(monkeypatch) -> None:
    """
    Given  a teacher's own review answer format and a model whose profile asks for compact JSON
    When   the review request is built
    Then   it is sent instead of the built-in format, the compact-JSON rule follows it, and the
           corrections-only format is unaffected
    """
    from teachers_teammate.domain.model_catalog import RequestProfile  # noqa: PLC0415

    system = _review_system(
        monkeypatch,
        answer_format_review="MY FORMAT",
        profile=RequestProfile(compact_json=True),
    )

    assert system.endswith(f"MY FORMAT\n{COMPACT_ANSWER_RULE}")
    assert REVIEW_FORMAT not in system


def test_text_mode_message_names_only_the_text_language(mocker) -> None:
    """
    Given  a corrector in text mode working on a German text
    When   the request is built
    Then   the user message names the text language, no explanation language, and the
           answer format forbids translation
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    corrector = NativeOllamaCorrector(
        model="m",
        client=_ollama_client(),
        settings=CorrectionSettings(output_mode="text"),
    )
    corrector.correct("~~a~~ b", "German")

    assert mock_chat.call_args.args[1] == "Text language: German\n\nText:\n\n~~a~~ b"
    assert "never translate the student's text" in mock_chat.call_args.kwargs["system"]


def test_review_mode_returns_the_corrected_text_and_its_review(monkeypatch) -> None:
    """
    Given  a model answer that is valid JSON for the schema
    When   correct() runs in review mode
    Then   the corrected text is spliced into the source and the review is returned
    """
    answer = CorrectionAnswer.model_validate_json(_answer_json())
    monkeypatch.setattr(
        correction_module,
        "invoke_langchain_structured",
        lambda *_args, **_kwargs: StructuredResult(parsed=answer, raw=_answer_json()),
    )

    outcome = LangChainCorrector(FakeListChatModel(responses=[])).correct("Helo wrold.", "English")

    assert outcome.text == "Hello world."
    assert outcome.warning is None
    assert outcome.review is not None
    assert outcome.review.sentences[0].changes[0].explanation == "Misspelt word."
    assert outcome.review.unplaced == ()


def test_invalid_review_answer_is_repaired_once(monkeypatch) -> None:
    """
    Given  a model that first answers with unusable JSON and then with a valid answer
    When   correct() runs in review mode
    Then   exactly one repair request is sent and it quotes the unusable answer
    """
    answer = CorrectionAnswer.model_validate_json(_answer_json())
    humans: list[str] = []

    def fake(_llm, **kwargs):
        humans.append(kwargs["human"])
        if len(humans) == 1:
            return StructuredResult(raw="not json at all", error="(invalid)", failure="invalid")
        return StructuredResult(parsed=answer, raw=_answer_json())

    monkeypatch.setattr(correction_module, "invoke_langchain_structured", fake)

    outcome = LangChainCorrector(FakeListChatModel(responses=[])).correct("Helo wrold.", "English")

    assert len(humans) == 2
    assert "not json at all" in humans[1]
    assert outcome.review is not None
    assert outcome.text == "Hello world."


def test_unrepairable_review_answer_falls_back_to_text_mode(monkeypatch) -> None:
    """
    Given  a model that never answers validly and a text-mode answer that works
    When   correct() runs in review mode
    Then   the correction is shown without a review and the fallback warning is returned
    """
    monkeypatch.setattr(
        correction_module,
        "invoke_langchain_structured",
        lambda *_args, **_kwargs: StructuredResult(
            raw="garbage", error="(invalid)", failure="invalid"
        ),
    )
    llm = FakeListChatModel(responses=["Hello world."])

    outcome = LangChainCorrector(llm).correct("Helo wrold.", "English")

    assert outcome.text == "Hello world."
    assert outcome.review is None
    assert outcome.warning == FALLBACK_WARNING


def test_review_transport_error_keeps_the_source_text(monkeypatch) -> None:
    """
    Given  a provider error with no raw answer at all
    When   correct() runs in review mode
    Then   the source text is kept and the provider error is reported instead of falling back
    """
    monkeypatch.setattr(
        correction_module,
        "invoke_langchain_structured",
        lambda *_args, **_kwargs: StructuredResult(
            error="Correction failed (boom); keeping original text.", failure="transport"
        ),
    )

    outcome = LangChainCorrector(FakeListChatModel(responses=[])).correct("raw text", "English")

    assert outcome.text == "raw text"
    assert outcome.review is None
    assert outcome.warning == "Correction failed (boom); keeping original text."


def test_review_answer_that_cannot_be_placed_falls_back_to_text_mode(monkeypatch) -> None:
    """
    Given  a model whose only sentence appears nowhere in the source text
    When   correct() runs in review mode
    Then   the file is corrected in text mode and the fallback warning is returned
    """
    answer = CorrectionAnswer.model_validate_json(_answer_json())
    monkeypatch.setattr(
        correction_module,
        "invoke_langchain_structured",
        lambda *_args, **_kwargs: StructuredResult(parsed=answer, raw=_answer_json()),
    )
    llm = FakeListChatModel(responses=["Unrelated."])

    outcome = LangChainCorrector(llm).correct("Completely different words.", "English")

    assert outcome.text == "Unrelated."
    assert outcome.review is None
    assert outcome.warning == FALLBACK_WARNING


def test_ollama_review_mode_constrains_the_answer_to_the_schema(mocker) -> None:
    """
    Given  a NativeOllamaCorrector whose server supports schema-constrained answers
    When   correct() is called and the answer is valid
    Then   the schema is passed as the format parameter and the review is returned
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value=_answer_json())
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    outcome = corrector.correct("Helo wrold.", "English")

    assert outcome.review is not None
    assert mock_chat.call_args.kwargs["format"] == schema_json()


def test_provider_without_structured_output_falls_back_to_text_mode() -> None:
    """
    Given  a chat model that cannot bind a schema at all (no tool calling)
    When   correct() runs in review mode
    Then   the file is corrected in text mode and the fallback warning is returned,
           instead of the source text being kept
    """
    llm = FakeListChatModel(responses=["Hello world."])

    outcome = LangChainCorrector(llm).correct("Helo wrold.", "English")

    assert outcome.text == "Hello world."
    assert outcome.review is None
    assert outcome.warning == FALLBACK_WARNING


def test_empty_review_answer_falls_back_without_a_repair_request(monkeypatch) -> None:
    """
    Given  a model that answers with nothing usable at all (empty raw answer, invalid)
    When   correct() runs in review mode
    Then   no repair request is sent -- there is nothing to quote -- and text mode takes over
    """
    calls: list[str] = []

    def fake(_llm, **kwargs):
        calls.append(kwargs["human"])
        return StructuredResult(raw="", error="(empty)", failure="invalid")

    monkeypatch.setattr(correction_module, "invoke_langchain_structured", fake)
    llm = FakeListChatModel(responses=["Hello world."])

    outcome = LangChainCorrector(llm).correct("Helo wrold.", "English")

    assert len(calls) == 1
    assert outcome.text == "Hello world."
    assert outcome.warning == FALLBACK_WARNING


def test_langchain_transport_error_keeps_the_complete_warning(monkeypatch) -> None:
    """
    Given  a provider whose request raises (for example an authentication error)
    When   correct() runs in review mode
    Then   the source text is kept and the warning has the usual "Correction failed" wording
    """

    from langchain_core.runnables import RunnableLambda  # noqa: PLC0415

    def boom(_prompt):
        raise ConnectionError("401 unauthorized")

    class _Broken(FakeListChatModel):
        def with_structured_output(self, *_args, **_kwargs):
            return RunnableLambda(boom)

    outcome = LangChainCorrector(_Broken(responses=[])).correct("raw text", "English")

    assert outcome.text == "raw text"
    assert outcome.review is None
    assert outcome.warning == "Correction failed (401 unauthorized); keeping original text."


def test_provider_rejecting_the_schema_request_falls_back_to_text_mode() -> None:
    """
    Given  a provider that binds a schema but answers the request itself with HTTP 400
    When   correct() runs in review mode
    Then   the file is corrected in text mode instead of keeping the original text
    """
    from langchain_core.runnables import RunnableLambda  # noqa: PLC0415

    class _BadRequest(Exception):
        status_code = 400

    def reject(_prompt):
        raise _BadRequest("response_format json_schema is not supported by this model")

    class _NoSchema(FakeListChatModel):
        def with_structured_output(self, *_args, **_kwargs):
            return RunnableLambda(reject)

    outcome = LangChainCorrector(_NoSchema(responses=["Hello world."])).correct(
        "Helo wrold.", "English"
    )

    assert outcome.text == "Hello world."
    assert outcome.review is None
    assert outcome.warning == FALLBACK_WARNING


def test_empty_ollama_review_answer_falls_back_to_text_mode(mocker) -> None:
    """
    Given  an Ollama model that answers the schema request with an empty string
    When   correct() runs in review mode
    Then   the corrections-only fallback runs and no raw validation error reaches the warning
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mocker.patch.object(OllamaClient, "chat", side_effect=["", "Hello world."])
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    outcome = corrector.correct("Helo wrold.", "English")

    assert outcome.text == "Hello world."
    assert outcome.review is None
    assert outcome.warning == FALLBACK_WARNING


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("", False),
        ("german", False),
        ("Correct to level {level}.", False),
        ("Correct the text.", True),
    ],
)
def test_prompt_ignores_level_only_for_custom_prompts_without_the_placeholder(
    prompt: str, expected: bool
) -> None:
    """
    Given  an empty prompt, a preset name, and custom prompts with and without {level}
    When   prompt_ignores_level() is called
    Then   only the custom prompt without the placeholder ignores the level setting
    """
    assert prompt_ignores_level(prompt) is expected


# ── Timeout ────────────────────────────────────────────────────────────────


def test_review_timeout_grows_with_the_text_and_is_capped() -> None:
    """
    Given  an OCR timeout and texts of different lengths
    When   the review timeout is computed
    Then   it is never below the OCR timeout, grows with the text and stops at the cap
    """
    assert _review_timeout(120.0, "x" * 100) == 120.0
    assert _review_timeout(10.0, "x" * 1000) == 110.0
    assert _review_timeout(10.0, "x" * 100_000) == REVIEW_TIMEOUT_CAP
    # The cap bounds the growth with the text, not a timeout the user chose themselves.
    assert _review_timeout(900.0, "x" * 100) == 900.0


# ── Tidying of the answer ──────────────────────────────────────────────────


def test_correct_strips_reasoning_and_a_code_fence_from_the_answer() -> None:
    """
    Given  a model that answers with a  thinking block and the text inside a code fence
    When   correct() is called on plain text
    Then   the stored correction is only the text, without reasoning or backticks
    """
    llm = FakeListChatModel(responses=[" thinkingcheck</think>\n```\nHello world.\n```"])

    outcome = LangChainCorrector(llm, settings=_TEXT_ONLY).correct("Helo wrold.", "English")

    assert outcome.text == "Hello world."
    assert outcome.warning is None


def test_correct_writes_a_markdown_answer_in_canonical_markdown() -> None:
    """
    Given  Markdown text and a model that wraps its answer in a ```markdown fence and
           uses an HTML strike tag instead of ~~
    When   correct(text_format="markdown") is called
    Then   the correction is canonical Markdown that parses like the original
    """
    llm = FakeListChatModel(responses=["```markdown\nI <strike>hate</strike> **love** this\n```"])

    outcome = LangChainCorrector(llm, settings=_TEXT_ONLY).correct(
        "I ~~hate~~ **lov** this", "English", text_format="markdown"
    )

    assert outcome.text == "I ~~hate~~ **love** this"
    assert outcome.warning is None


def test_correct_removes_markup_the_model_added_to_plain_text() -> None:
    """
    Given  plain text and a model that answers with **bold** words
    When   correct() is called without a text format
    Then   the correction contains no asterisks
    """
    llm = FakeListChatModel(responses=["This is **very** good."])

    outcome = LangChainCorrector(llm, settings=_TEXT_ONLY).correct("This is very god.", "English")

    assert outcome.text == "This is very good."


def test_native_ollama_corrector_tidies_the_answer(mocker) -> None:
    """
    Given  a NativeOllamaCorrector whose model answers inside a code fence
    When   correct() is called in text mode
    Then   the fence is removed from the returned correction
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mocker.patch.object(OllamaClient, "chat", return_value="```\nfixed text\n```")
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client(), settings=_TEXT_ONLY)

    outcome = corrector.correct("fixd text", "English")

    assert outcome.text == "fixed text"
    assert outcome.warning is None


def test_correct_keeps_the_original_text_untouched_when_the_model_fails() -> None:
    """
    Given  a model that fails and Markdown text that would change if it were tidied
    When   correct(text_format="markdown") is called
    Then   the fallback is the exact original text
    """
    original = "- a\n- b <strike>c</strike>"

    outcome = LangChainCorrector(FakeListChatModel(responses=[]), settings=_TEXT_ONLY).correct(
        original, "English", text_format="markdown"
    )

    assert outcome.text == original
    assert outcome.warning is not None


def test_text_mode_provider_error_is_marked_as_failed(mocker) -> None:
    """
    Given  an Ollama server that cannot be reached in corrections-only mode
    When   correct() is called
    Then   the source text is kept with a warning and the outcome is marked as failed
    """
    from teachers_teammate.exceptions import OCRError  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mocker.patch.object(OllamaClient, "chat", side_effect=OCRError("cannot connect"))
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client(), settings=_TEXT_ONLY)

    outcome = corrector.correct("raw text", "English")

    assert outcome.text == "raw text"
    assert outcome.warning is not None
    assert outcome.failed is True


def test_successful_and_fallback_outcomes_are_not_marked_as_failed(mocker) -> None:
    """
    Given  a model that cannot answer the schema but corrects in text mode
    When   correct() is called
    Then   the corrections-only fallback is a finished result, not a failure
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mocker.patch.object(OllamaClient, "chat", side_effect=["", "Hello world."])
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    outcome = corrector.correct("Helo wrold.", "English")

    assert outcome.warning == FALLBACK_WARNING
    assert outcome.failed is False


def test_ollama_server_rejecting_the_format_falls_back_to_text_mode(mocker) -> None:
    """
    Given  an older Ollama server that answers the schema request with HTTP 400
    When   correct() runs in review mode
    Then   the file is corrected in text mode instead of being reported as a network failure
    """
    from teachers_teammate.exceptions import OCRError  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mocker.patch.object(
        OllamaClient,
        "chat",
        side_effect=[OCRError("HTTP error from Ollama: invalid format", status_code=400), "Fixed."],
    )
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    outcome = corrector.correct("Fixd.", "English")

    assert outcome.text == "Fixed."
    assert outcome.warning == FALLBACK_WARNING
    assert outcome.failed is False


# ── Cut-off answers (done_reason="length") ─────────────────────────────────


def _truncated_client(mocker, calls: list[str]):
    """Return an OllamaClient whose chat() records each call and is cut off every time."""
    from teachers_teammate.exceptions import ResponseTruncatedError  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    def _cut_off(_self, _model, prompt, **_kwargs):
        calls.append(prompt)
        raise ResponseTruncatedError("The text was too long for the model's working memory.")

    mocker.patch.object(OllamaClient, "chat", autospec=True, side_effect=_cut_off)
    return _ollama_client()


def test_two_cut_offs_fall_back_to_text_mode(mocker) -> None:
    """
    Given  an Ollama model whose review answer is cut off by the context window twice, and text
           mode that is cut off as well
    When   correct() runs in review mode
    Then   the source text is kept, the working-memory warning is shown, the file is marked
           failed
    And    no repair request is sent: the review request twice, then one text-mode request
    """
    calls: list[str] = []
    corrector = NativeOllamaCorrector(model="m", client=_truncated_client(mocker, calls))

    outcome = corrector.correct("Ein langer Text.", "German")

    assert len(calls) == 3
    assert "Explanation language" in calls[0]
    assert "Explanation language" in calls[1]
    assert "Explanation language" not in calls[2]
    assert outcome.text == "Ein langer Text."
    assert outcome.review is None
    assert outcome.warning is not None
    assert TRUNCATED_MESSAGE in outcome.warning
    assert outcome.failed is True


def test_a_cut_off_review_is_asked_once_more(mocker) -> None:
    """
    Given  an Ollama model whose first review answer is cut off and whose second one is valid
    When   correct() runs in review mode
    Then   the review of the second answer is kept, with no fallback and no warning
    And    the same review request was sent twice, and text mode was never asked
    """
    from teachers_teammate.exceptions import ResponseTruncatedError  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    calls: list[str] = []
    answer = json.dumps(
        {
            "sentences": [
                {
                    "original": "Ein langer Text.",
                    "corrected": "Ein langer Text!",
                    "changes": [
                        {
                            "original": ".",
                            "corrected": "!",
                            "category": "punctuation",
                            "explanation": "Ein Ausrufezeichen passt hier.",
                        }
                    ],
                }
            ],
            "unclear": [],
        }
    )

    def _first_cut_off(_self, _model, prompt, **_kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            raise ResponseTruncatedError("The text was too long for the model's working memory.")
        return answer

    mocker.patch.object(OllamaClient, "chat", autospec=True, side_effect=_first_cut_off)
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    outcome = corrector.correct("Ein langer Text.", "German")

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert outcome.review is not None
    assert outcome.text == "Ein langer Text!"
    assert outcome.failed is False
    assert outcome.warning is None


def test_a_cut_off_repair_is_not_retried(mocker) -> None:
    """
    Given  an Ollama model whose first review answer does not validate and whose repair answer
           is cut off
    When   correct() runs in review mode
    Then   the file falls back to text mode without a third review request: the repair request
           is the longest of all, so a second try would not make it shorter
    """
    from teachers_teammate.exceptions import ResponseTruncatedError  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    calls: list[str] = []

    def _invalid_then_cut_off(_self, _model, prompt, **_kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return "not JSON at all"
        if len(calls) == 2:
            raise ResponseTruncatedError("The text was too long for the model's working memory.")
        return "Ein korrigierter Text."

    mocker.patch.object(OllamaClient, "chat", autospec=True, side_effect=_invalid_then_cut_off)
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    outcome = corrector.correct("Ein langer Text.", "German")

    assert len(calls) == 3
    assert "Explanation language" in calls[0]
    assert "Explanation language" in calls[1]
    assert "Explanation language" not in calls[2]
    assert outcome.review is None
    assert outcome.text == "Ein korrigierter Text."
    assert outcome.warning is not None
    assert FALLBACK_WARNING in outcome.warning


def test_ollama_text_mode_keeps_the_original_text_when_the_answer_is_cut_off(mocker) -> None:
    """
    Given  an Ollama model in corrections-only mode whose answer is cut off
    When   correct() is called
    Then   the original text is returned (not the truncated answer), with the warning, failed
    """
    calls: list[str] = []
    corrector = NativeOllamaCorrector(
        model="m", client=_truncated_client(mocker, calls), settings=_TEXT_ONLY
    )

    outcome = corrector.correct("Ein langer Text.", "German")

    assert len(calls) == 1
    assert outcome.text == "Ein langer Text."
    assert outcome.warning == TRUNCATED_MESSAGE
    assert outcome.failed is True


def test_ollama_requests_carry_the_fixed_proofreading_context_size(mocker) -> None:
    """
    Given  a NativeOllamaCorrector in review and in text mode
    When   correct() is called
    Then   both requests send the shared num_ctx of the proofreading options
    """
    from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
        PROOFREADING_NUM_CTX,
        OllamaClient,
    )

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value=_answer_json())
    NativeOllamaCorrector(model="m", client=_ollama_client()).correct("Helo wrold.", "English")
    review_options = mock_chat.call_args.kwargs["options"]

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    NativeOllamaCorrector(model="m", client=_ollama_client(), settings=_TEXT_ONLY).correct(
        "Helo wrold.", "English"
    )
    text_options = mock_chat.call_args.kwargs["options"]

    assert review_options["num_ctx"] == PROOFREADING_NUM_CTX
    assert text_options["num_ctx"] == PROOFREADING_NUM_CTX


def test_ollama_text_budget_is_smaller_for_markdown_than_for_plain_text() -> None:
    """
    Given  an Ollama corrector in review mode
    When   its text budget is asked for plain text and for Markdown
    Then   the Markdown budget is smaller, because its system prompt carries a notation hint
    """
    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())

    plain = corrector.max_text_chars("German")
    markdown = corrector.max_text_chars("German", text_format="markdown")

    assert 0 < markdown < plain


@pytest.mark.parametrize("text_format", ["plain", "markdown"])
def test_a_text_at_the_budget_leaves_room_for_a_longer_review_answer(
    text_format: TextFormat,
) -> None:
    """
    Given  an Ollama corrector and a text exactly as long as its budget
    When   the room left in the window is worked out
    Then   the request and an answer REVIEW_ANSWER_FACTOR times the text fit the window: the
           answer grows with the text, not with the prompt
    """
    from teachers_teammate.domain.context_budget import (  # noqa: PLC0415
        REVIEW_ANSWER_FACTOR,
        ContextBudget,
    )

    corrector = NativeOllamaCorrector(model="m", client=_ollama_client())
    limit = corrector.max_text_chars("German", text_format=text_format)
    system, prompt = corrector._messages("x" * limit, "German", text_format, mode="review")
    text_tokens = -(-limit // 3)

    assert ContextBudget(corrector._num_ctx).request_fits(
        len(system) + len(prompt), answer_tokens=int(text_tokens * REVIEW_ANSWER_FACTOR)
    )


def test_one_cut_off_try_goes_straight_to_text_mode(mocker) -> None:
    """
    Given  an Ollama corrector set to a single try (an online model, or temperature 0)
    When   its review answer is cut off and text mode is cut off as well
    Then   the review request was sent once, then one text-mode request
    """
    calls: list[str] = []
    corrector = NativeOllamaCorrector(
        model="m",
        client=_truncated_client(mocker, calls),
        settings=CorrectionSettings(cut_off_tries=1),
    )

    corrector.correct("Ein langer Text.", "German")

    assert len(calls) == 2
    assert "Explanation language" in calls[0]
    assert "Explanation language" not in calls[1]


def test_request_messages_are_what_the_native_corrector_sends(mocker) -> None:
    """
    Given  a native Ollama corrector with a profile that asks for the compact rule
    When   it proofreads a text, and request_messages() builds the request for the same text
    Then   the system and user messages it sent are exactly request_messages()'
    """
    from teachers_teammate.domain.model_catalog import RequestProfile  # noqa: PLC0415
    from teachers_teammate.exceptions import ResponseTruncatedError  # noqa: PLC0415
    from teachers_teammate.infrastructure.correction import request_messages  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    sent: list[tuple[str, str]] = []

    def _record(_self, _model, prompt, *, system="", **_kwargs):
        sent.append((system, prompt))
        raise ResponseTruncatedError("cut off")

    mocker.patch.object(OllamaClient, "chat", autospec=True, side_effect=_record)
    corrector = NativeOllamaCorrector(
        model="m",
        client=_ollama_client(),
        settings=CorrectionSettings(cut_off_tries=1),
        profile=RequestProfile(compact_json=True),
    )

    corrector.correct("Ein Text.", "German")

    assert sent[0] == request_messages("Ein Text.", "German", compact_json=True)
    assert sent[1] == request_messages("Ein Text.", "German", mode="text")


def test_a_short_text_goes_out_whole_and_a_long_one_in_pieces() -> None:
    """
    Given  a chunking corrector around a corrector with a small text budget
    When   split() is asked about a text that fits and one that does not
    Then   the first is None (whole) and the second the pieces correct() would send
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from teachers_teammate.infrastructure.correction import ChunkingCorrector  # noqa: PLC0415

    inner = MagicMock()
    inner.max_text_chars.return_value = 40
    chunking = ChunkingCorrector(inner)

    assert chunking.split("Short text.", "German") is None
    pieces = chunking.split("First sentence here. Second sentence here. Third one.", "German")
    assert pieces is not None and len(pieces) >= 2
    assert "".join(piece.text for piece in pieces).replace(" ", "") != ""
