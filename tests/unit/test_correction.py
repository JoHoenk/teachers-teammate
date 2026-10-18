"""Unit tests for teachers_teammate.correction."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from teachers_teammate.infrastructure.correction import (
    ENGLISH_PROMPT,
    GERMAN_PROMPT,
    LangChainCorrector,
    _resolve_prompt,
)

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


# ── LangChainCorrector.correct ─────────────────────────────────────────────


def test_correct_returns_llm_response() -> None:
    """
    Given  a LangChainCorrector backed by a FakeListChatModel with one response
    When   correct() is called with raw OCR text
    Then   the pre-canned LLM response is returned
    """
    llm = FakeListChatModel(responses=["Hello world."])
    corrector = LangChainCorrector(llm)
    result = corrector.correct("Helo wrold.", "English")
    assert result == ("Hello world.", None)


def test_correct_returns_original_on_exception() -> None:
    """
    Given  a LangChainCorrector backed by a FakeListChatModel with an exhausted response list
    When   correct() is called (causing the model to raise an IndexError internally)
    Then   the original raw text is returned with a warning message
    """
    # FakeListChatModel raises IndexError when responses list is exhausted.
    # LangChainCorrector must catch and return the original text.
    llm = FakeListChatModel(responses=[])
    corrector = LangChainCorrector(llm)
    text, warning = corrector.correct("some text", "English")
    assert text == "some text"
    assert warning is not None
    assert "Correction failed" in warning


def test_correct_returns_string_for_multiple_calls() -> None:
    """
    Given  a LangChainCorrector backed by a FakeListChatModel with two responses
    When   correct() is called twice
    Then   each call returns the next pre-canned response in order
    """
    llm = FakeListChatModel(responses=["First.", "Second."])
    corrector = LangChainCorrector(llm)
    assert corrector.correct("first input", "English") == ("First.", None)
    assert corrector.correct("second input", "English") == ("Second.", None)


def test_correct_preset_name_at_init_resolves_correctly() -> None:
    """
    Given  a LangChainCorrector initialised with prompt="german"
    When   the stored _prompt is passed back through _resolve_prompt
    Then   it resolves to GERMAN_PROMPT regardless of the call-time language
    """
    llm = FakeListChatModel(responses=["OK"])
    corrector = LangChainCorrector(llm, prompt="german")
    # _resolve_prompt("german", any_language) → GERMAN_PROMPT
    assert _resolve_prompt(corrector._prompt, "English") == GERMAN_PROMPT


# ── NativeOllamaCorrector ──────────────────────────────────────────────────


def test_native_ollama_corrector_passes_temperature_in_options(mocker) -> None:
    """
    Given  a NativeOllamaCorrector constructed with temperature=0.3
    When   correct() is called
    Then   OllamaClient.chat receives options={"temperature": 0.3}
    """
    from teachers_teammate.infrastructure.correction import NativeOllamaCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="fixed text")
    client = OllamaClient("http://localhost:11434")
    corrector = NativeOllamaCorrector(model="m", client=client, temperature=0.3)

    result, warning = corrector.correct("raw", "English")

    assert result == "fixed text"
    assert warning is None
    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.3


def test_native_ollama_corrector_default_temperature(mocker) -> None:
    """
    Given  a NativeOllamaCorrector constructed without an explicit temperature
    When   correct() is called
    Then   OllamaClient.chat receives options={"temperature": 0.7}
    """
    from teachers_teammate.infrastructure.correction import NativeOllamaCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    client = OllamaClient("http://localhost:11434")
    corrector = NativeOllamaCorrector(model="m", client=client)

    corrector.correct("text", "English")

    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.7


# ── Markdown notation hint ─────────────────────────────────────────────────


def _captured_ollama_prompt(mocker, text_format: str | None) -> str:
    from teachers_teammate.infrastructure.correction import NativeOllamaCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    corrector = NativeOllamaCorrector(model="m", client=OllamaClient("http://localhost:11434"))
    if text_format is None:
        corrector.correct("~~a~~ b", "English")
    else:
        corrector.correct("~~a~~ b", "English", text_format=text_format)  # ty: ignore[invalid-argument-type]  # parametrised literal
    return mock_chat.call_args.args[1]


def test_native_ollama_corrector_explains_markdown_notation(mocker) -> None:
    """
    Given  a NativeOllamaCorrector and text flagged as Markdown
    When   correct(text_format="markdown") is called
    Then   the prompt tells the model what ~~strikethrough~~ and <sup> mean and to keep them
    """
    prompt = _captured_ollama_prompt(mocker, "markdown")

    assert "~~text~~ marks words the writer crossed out" in prompt
    assert "<sup>text</sup>" in prompt
    assert prompt.startswith(ENGLISH_PROMPT)


def test_native_ollama_corrector_prompt_unchanged_for_plain_text(mocker) -> None:
    """
    Given  a NativeOllamaCorrector and plain text (explicit or default format)
    When   correct() is called
    Then   no notation hint is added, so plain-text prompts stay exactly as before
    """
    explicit = _captured_ollama_prompt(mocker, "plain")
    default = _captured_ollama_prompt(mocker, None)

    assert explicit == default
    assert "Markdown" not in explicit


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

    LangChainCorrector(object()).correct("x", "English", text_format="markdown")  # ty: ignore[invalid-argument-type]  # stub llm

    system = next(content for role, content in captured if role == "system")
    assert system.startswith(ENGLISH_PROMPT)
    assert "crossed out" in system


# ── Tidying of the answer ──────────────────────────────────────────────────


def test_correct_strips_reasoning_and_a_code_fence_from_the_answer() -> None:
    """
    Given  a model that answers with a <think> block and the text inside a code fence
    When   correct() is called on plain text
    Then   the stored correction is only the text, without reasoning or backticks
    """
    llm = FakeListChatModel(responses=["<think>check</think>\n```\nHello world.\n```"])

    result = LangChainCorrector(llm).correct("Helo wrold.", "English")

    assert result == ("Hello world.", None)


def test_correct_writes_a_markdown_answer_in_canonical_markdown() -> None:
    """
    Given  Markdown text and a model that wraps its answer in a ```markdown fence and
           uses an HTML strike tag instead of ~~
    When   correct(text_format="markdown") is called
    Then   the correction is canonical Markdown that parses like the original
    """
    llm = FakeListChatModel(responses=["```markdown\nI <strike>hate</strike> **love** this\n```"])

    text, warning = LangChainCorrector(llm).correct(
        "I ~~hate~~ **lov** this", "English", text_format="markdown"
    )

    assert (text, warning) == ("I ~~hate~~ **love** this", None)


def test_correct_removes_markup_the_model_added_to_plain_text() -> None:
    """
    Given  plain text and a model that answers with **bold** words
    When   correct() is called without a text format
    Then   the correction contains no asterisks
    """
    llm = FakeListChatModel(responses=["This is **very** good."])

    text, _warning = LangChainCorrector(llm).correct("This is very god.", "English")

    assert text == "This is very good."


def test_native_ollama_corrector_tidies_the_answer(mocker) -> None:
    """
    Given  a NativeOllamaCorrector whose model answers inside a code fence
    When   correct() is called
    Then   the fence is removed from the returned correction
    """
    from teachers_teammate.infrastructure.correction import NativeOllamaCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mocker.patch.object(OllamaClient, "chat", return_value="```\nfixed text\n```")
    corrector = NativeOllamaCorrector(model="m", client=OllamaClient("http://localhost:11434"))

    assert corrector.correct("fixd text", "English") == ("fixed text", None)


def test_correct_keeps_the_original_text_untouched_when_the_model_fails() -> None:
    """
    Given  a model that fails and Markdown text that would change if it were tidied
    When   correct(text_format="markdown") is called
    Then   the fallback is the exact original text
    """
    original = "- a\n- b <strike>c</strike>"

    text, warning = LangChainCorrector(FakeListChatModel(responses=[])).correct(
        original, "English", text_format="markdown"
    )

    assert text == original
    assert warning is not None
