"""A run's engines: the requests they send, and what they keep of the answers."""

from __future__ import annotations

import base64
from collections.abc import Iterator
import dataclasses
from pathlib import Path

import pytest

from teachers_teammate.domain.ocr_prompt import get_ocr_prompt
from teachers_teammate.domain.request_profile import OCR_TEMPERATURE
from teachers_teammate.infrastructure.ocr_processor import OCR_ANSWER_TOKENS, OllamaOCRProcessor
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.performance_probe import WARMUP_ANSWER_TOKENS
from teachers_teammate.infrastructure.ppocr.detect import DetectorSettings
from tools.ocr_eval import engine, records

from ..fakes.fake_ollama import CHAT_ANSWER, MODEL_SIZE, FakeOllama

TAG = "qwen3-vl:8b"
CARD_TAG = "glm-ocr:q8_0"
"""A catalogued model whose card's prompt is transcribed."""


@pytest.fixture
def fake() -> Iterator[FakeOllama]:
    """Return a server that holds the two reading models, closed after the test."""
    with FakeOllama() as server:
        for tag in (TAG, CARD_TAG):
            server.install(tag, ("completion", "vision"))
        yield server


def _page(root: Path) -> Path:
    """Write one page and return it."""
    image = root / "s01.png"
    image.write_bytes(b"page s01")
    return image


def _chats(fake: FakeOllama) -> list[dict]:
    """Return the bodies of the chat requests the server was sent, in order."""
    return [request.body for request in fake.requests if request.path == "/api/chat"]


def test_a_cpu_only_model_asks_the_processor_for_both_requests(
    tmp_path: Path, fake: FakeOllama
) -> None:
    """Given a model kept off the card, when it warms up and reads, then both requests say so."""
    model = engine.Model(OllamaClient(fake.url), CARD_TAG, prompt=records.CARD, cpu_only=True)

    model.warm_up()
    model.read(_page(tmp_path), "German")

    warm_up, read = _chats(fake)
    assert warm_up["options"] == {"num_predict": WARMUP_ANSWER_TOKENS, "num_gpu": 0}
    assert read["options"] == {
        "temperature": OCR_TEMPERATURE,
        "num_predict": OCR_ANSWER_TOKENS,
        "num_gpu": 0,
    }


def test_a_model_on_the_card_names_no_device(tmp_path: Path, fake: FakeOllama) -> None:
    """Given a model on the card, when it warms up and reads, then no request says where it runs."""
    model = engine.Model(OllamaClient(fake.url), TAG)

    model.warm_up()
    model.read(_page(tmp_path), "German")

    assert all("num_gpu" not in body["options"] for body in _chats(fake))


def test_a_model_keeps_what_the_server_said_of_its_answer(tmp_path: Path, fake: FakeOllama) -> None:
    """Given a page and a model the server holds in memory
    When   the model reads the page
    Then   the request holds the page's bytes base64, and the answer keeps the text, its tokens,
           why it stopped and the model's memory
    """
    fake.loaded(TAG)
    page = _page(tmp_path)

    answer = engine.Model(OllamaClient(fake.url), TAG).read(page, "German")

    (body,) = _chats(fake)
    assert body["messages"][-1]["images"] == [base64.b64encode(page.read_bytes()).decode("utf-8")]
    assert answer.text == CHAT_ANSWER
    assert answer.answer_tokens == 2
    assert not answer.cut_off
    assert answer.memory_mb == round(MODEL_SIZE / 1024**2)


def test_the_model_asks_the_page_the_way_the_app_asks_it(tmp_path: Path, fake: FakeOllama) -> None:
    """Given one page and one model
    When   the app's OCR processor reads it, and this tool's model reads it
    Then   both send the same request — model, prompt, image, options, thinking — so a figure
           measures what a teacher's run would get
    """
    page = _page(tmp_path)
    client = OllamaClient(fake.url)

    OllamaOCRProcessor(TAG, client).process_image(page, "German")
    engine.Model(client, TAG).read(page, "German")

    asked_by_the_app, asked_by_the_tool = _chats(fake)
    assert asked_by_the_tool == asked_by_the_app


def test_a_model_asks_each_page_in_its_language_and_the_server_once(fake: FakeOllama) -> None:
    """Given a model asked with the app's prompt
    When   it is asked what it sends for a German and an English page
    Then   each prompt names its language, and the server is asked for the digest only once
    """
    model = engine.Model(OllamaClient(fake.url), TAG)

    german = model.ask("German")
    asked = len(fake.requests)
    english = model.ask("English")

    assert german.prompt == get_ocr_prompt("German")
    assert english.prompt == get_ocr_prompt("English")
    assert german.digest == english.digest != ""
    assert len(fake.requests) == asked


def test_a_card_prompt_is_the_same_in_every_language(fake: FakeOllama) -> None:
    """Given a model asked with its card's prompt
    When   it is asked what it sends for pages of two languages
    Then   it is the card's prompt both times
    """
    model = engine.Model(OllamaClient(fake.url), CARD_TAG, prompt=records.CARD)

    assert model.ask("German").prompt == model.ask("English").prompt == "Text Recognition:"


def test_a_card_prompt_nobody_transcribed_is_refused(fake: FakeOllama) -> None:
    """Given a model whose card's prompt is not transcribed
    When   it is built to be asked with its card's prompt
    Then   it is refused, before any page is sent
    """
    with pytest.raises(ValueError, match="no card prompt"):
        engine.Model(OllamaClient(fake.url), TAG, prompt=records.CARD)

    assert _chats(fake) == []


def test_an_answer_the_deadline_ended_is_cut_off() -> None:
    """Given an answer the run's deadline ended, and one the token limit ended
    When   whether it was cut off is asked
    Then   both were: neither reached the end of the page
    """
    assert engine.Answer("", done_reason=engine.DEADLINE).cut_off
    assert engine.Answer("Der Hund", done_reason="length").cut_off
    assert not engine.Answer("Der Hund", done_reason="stop").cut_off


def test_every_detector_number_can_be_set_by_its_name() -> None:
    """Given every name the detector has, each set to another number
    When   the app's settings are made from them
    Then   each number is carried in its field's own type, and a record spells them all back
    """
    wanted = {name: float(index + 1) for index, name in enumerate(engine.DETECTOR, start=1)}

    settings = engine.detector_settings(tuple(wanted.items()))

    assert isinstance(settings.max_side, int)
    assert dict(engine.numbers_of(settings)) == wanted


def test_a_name_the_detector_has_not_is_refused() -> None:
    """Given an override for a setting the detector does not have
    When   the app's settings are made from it
    Then   it is refused, naming the settings there are
    """
    with pytest.raises(ValueError, match="despeckle"):
        engine.detector_settings((("despeckle", 3.0),))


def test_the_detector_s_numbers_are_the_app_s_fields() -> None:
    """Given the app's detector settings
    When   the names this tool takes overrides by are read
    Then   they are its fields, so a field the app adds is one this tool can set
    """
    assert engine.DETECTOR == tuple(field.name for field in dataclasses.fields(DetectorSettings))
