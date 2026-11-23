"""The fake Ollama server, checked against the ``ollama`` client the app is locked to.

The walkthrough tests drive the real client, the real threads and the real pull stream against
:class:`~tests.fakes.fake_ollama.FakeOllama`.  This file is what keeps that fake honest: a bump
of ``ollama`` that changes a response model fails *here*, with a readable message, instead of
hanging in the walkthrough.

It cannot say anything about what a real server answers — only that the fake and the client
still agree.
"""

from __future__ import annotations

import ollama
from collections.abc import Iterator
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from teachers_teammate.domain.correction_prompts import correct_user, explain_user, unclear_user
from teachers_teammate.domain.ollama_discovery import WaitKind
from teachers_teammate.infrastructure import ollama_utils
from teachers_teammate.infrastructure.ollama_utils import OllamaClient
from teachers_teammate.infrastructure.proofreading.schemas import (
    UnclearAnswer,
    explain_schema,
    parse_explain,
    parse_unclear,
)

from ..fakes.fake_ollama import (
    CHAT_ANSWER,
    MODEL_SIZE,
    PROOFREADING_CATEGORY,
    PULL_FAILED,
    PULL_MANIFEST,
    PULL_SUCCESS,
    FakeOllama,
    ProofreadingAnswers,
)


@pytest.fixture(name="fake")
def _fake() -> Iterator[FakeOllama]:
    """A fake server that stops answering when the test is over."""
    with FakeOllama() as server:
        yield server


def test_list_parses_the_installed_models(fake: FakeOllama) -> None:
    """
    Given  a server holding two models, one of them able to read images
    When   the app's client lists them
    Then   both tags come back, so the setup plan can tell what is already downloaded
    """
    fake.install("qwen3:8b")
    fake.install("qwen3-vl:8b", ("completion", "vision"))

    listed = ollama.Client(host=fake.url).list()

    assert [model.model for model in listed.models] == ["qwen3:8b", "qwen3-vl:8b"]
    assert all(model.size for model in listed.models)
    assert listed.models[1].details.families == ["clip"]


def test_show_gives_the_capabilities(fake: FakeOllama) -> None:
    """
    Given  an installed model that can read images
    When   the app asks the server to describe it
    Then   its capabilities say so, which is how a model outside the catalog is classified
    """
    fake.install("some-vision-model:latest", ("completion", "vision"))

    shown = ollama.Client(host=fake.url).show("some-vision-model:latest")

    assert shown.capabilities == ["completion", "vision"]


def test_show_of_an_unknown_model_is_a_404(fake: FakeOllama) -> None:
    """
    Given  a server that does not hold a tag
    When   the app asks the server to describe it
    Then   the client raises, which the app turns into "no information about this model"
    """
    with pytest.raises(ollama.ResponseError) as raised:
        ollama.Client(host=fake.url).show("not-installed:latest")

    assert raised.value.status_code == 404


def test_pull_streams_to_success_and_installs_the_tag(fake: FakeOllama) -> None:
    """
    Given  a server that does not hold a tag
    When   the app pulls it with the real client
    Then   the stream ends in "success" and the tag is listed afterwards
    """
    statuses = [part.status for part in ollama.Client(host=fake.url).pull("qwen3:8b", stream=True)]

    assert statuses[-1] == PULL_SUCCESS
    assert fake.pulled == ["qwen3:8b"]
    assert [model.model for model in ollama.Client(host=fake.url).list().models] == ["qwen3:8b"]


def test_the_apps_own_pull_reports_progress_and_installs_the_tag(fake: FakeOllama) -> None:
    """
    Given  a server that does not hold a tag
    When   the app's own pull runs, as the download thread runs it
    Then   it reports the bytes as they come and the tag is there afterwards
    """
    reported: list[tuple[str, float, float]] = []

    OllamaClient(fake.url).pull(
        "qwen3:8b", lambda status, done, total: reported.append((status, done, total))
    )

    assert fake.pulled == ["qwen3:8b"]
    assert reported[-1][0] == PULL_SUCCESS
    assert reported[-2][1:] == (1_000_000.0, 1_000_000.0)


def test_a_failing_tag_raises(fake: FakeOllama) -> None:
    """
    Given  a server that refuses to pull a tag
    When   the app pulls it
    Then   the client raises, so the Done page can say why the row failed
    """
    fake.fail_pull.add("broken:latest")

    with pytest.raises(ollama.ResponseError) as raised:
        list(ollama.Client(host=fake.url).pull("broken:latest", stream=True))

    assert PULL_FAILED in str(raised.value)
    assert fake.pulled == []


def test_the_apps_own_pull_turns_a_refusal_into_its_own_error(fake: FakeOllama) -> None:
    """
    Given  a server that refuses to pull a tag
    When   the app's own pull runs
    Then   it raises the app's error, which is what the download thread reports
    """
    from teachers_teammate.exceptions import OCRError  # noqa: PLC0415

    fake.fail_pull.add("broken:latest")

    with pytest.raises(OCRError) as raised:
        OllamaClient(fake.url).pull("broken:latest")

    assert PULL_FAILED in str(raised.value)


def test_the_fake_records_every_request_in_order(fake: FakeOllama) -> None:
    """
    Given  a server the app has asked for its models and for a description of one
    When   the requests are read back
    Then   they are there in order, with the JSON body the app sent
    """
    client = ollama.Client(host=fake.url)
    fake.install("qwen3:8b")
    client.list()
    client.show("qwen3:8b")

    assert fake.paths() == ["/api/tags", "/api/show"]
    assert fake.requests[1].method == "POST"
    assert fake.requests[1].body == {"model": "qwen3:8b"}


def test_a_failing_pull_says_it_is_pulling_before_it_fails(fake: FakeOllama) -> None:
    """
    Given  a server that refuses to pull a tag
    When   the app's own pull runs and reports what it is told
    Then   the first thing it hears is "pulling manifest", as from a real server, and then
           the error — so a row that failed shows the step it failed in
    """
    from teachers_teammate.exceptions import OCRError  # noqa: PLC0415

    fake.fail_pull.add("broken:latest")
    heard: list[str] = []

    with pytest.raises(OCRError):
        OllamaClient(fake.url).pull(
            "broken:latest", lambda status, _done, _total: heard.append(status)
        )

    assert heard == [PULL_MANIFEST]


def test_a_list_entry_carries_the_fields_a_real_server_always_sends(fake: FakeOllama) -> None:
    """
    Given  an installed model
    When   /api/tags is read as raw JSON, past the client's models
    Then   the entry has "modified_at", and its details "format" and "parent_model" — Ollama
           sends all three on every entry, so a reader that needs one must find it here too
    """
    fake.install("qwen3:8b")

    with urlopen(f"{fake.url}/api/tags", timeout=5) as reply:  # the fake answers on loopback
        entry = json.load(reply)["models"][0]

    assert entry["modified_at"]
    assert entry["details"]["format"] == "gguf"
    assert entry["details"]["parent_model"] == ""


def test_the_size_of_an_installed_model_reaches_the_list(fake: FakeOllama) -> None:
    """
    Given  one model installed with a size and one without
    When   the app's client lists them with their sizes
    Then   the first has its own size and the second the default one
    """
    fake.install("small:latest", size=1_000_000)
    fake.install("big:latest")

    assert OllamaClient(fake.url).list_models_with_size() == [
        ("small:latest", 1_000_000),
        ("big:latest", MODEL_SIZE),
    ]


# ── what the server is doing while a request has no answer yet ─────────────


@pytest.fixture(name="quick_wait")
def _quick_wait(monkeypatch) -> None:
    """Ask the server what it is doing every 0.2 s, and report after 0.1 s.

    The real figures are seconds; a test that waited them out would take longer than the whole
    suite.
    """
    monkeypatch.setattr(ollama_utils, "_WAITING_POLL_S", 0.05)
    monkeypatch.setattr(ollama_utils, "WAIT_DETAIL_AFTER_S", 0.1)


def test_a_request_queued_behind_another_model_says_so_before_it_answers(
    fake: FakeOllama, quick_wait: None
) -> None:
    """
    Given  a server still generating with one model while a request asks for another
    When   the app's client sends that request
    Then   it reports what the server is doing before the first token arrives, and the answer
           is marked as one that waited, so its load time is no measurement
    """
    fake.install("a:1b")
    fake.install("b:1b")
    fake.busy_with("a:1b", 0.8)
    client = OllamaClient(fake.url)
    events: list[tuple[str, object]] = []
    client.detail_sink = lambda detail: events.append(("detail", detail))
    client.first_token_sink = lambda: events.append(("token", None))

    answer = client.chat("b:1b", "hi")

    assert answer == CHAT_ANSWER
    details = [detail for kind, detail in events if kind == "detail"]
    assert details, "the run was never told what the server was doing"
    assert events[-1][0] == "token"
    assert any(detail.kind is WaitKind.BUSY and detail.other == "a:1b" for detail in details), (
        details
    )
    assert client.last_stats is not None
    assert client.last_stats.waited


def test_a_model_already_in_memory_says_nothing(fake: FakeOllama, quick_wait: None) -> None:
    """
    Given  a model that is in memory and busy with another request
    When   the app's client asks it for an answer
    Then   no detail is sent — it is reading the text, so the first token is close — and the
           answer is not marked as one that waited
    """
    fake.install("b:1b")
    fake.busy_with("b:1b", 0.8)
    client = OllamaClient(fake.url)
    details: list[object] = []
    client.detail_sink = details.append
    tokens: list[str] = []
    client.first_token_sink = lambda: tokens.append("token")

    assert client.chat("b:1b", "hi") == CHAT_ANSWER

    assert details == []
    assert tokens == ["token"]
    assert client.last_stats is not None
    assert not client.last_stats.waited
    # The server was read, and it did list the model: the silence is the answer, not a failure.
    assert client.activity("b:1b").has("b:1b")


def test_a_model_that_is_loading_is_reported_as_loading(fake: FakeOllama, quick_wait: None) -> None:
    """
    Given  a model that is not in memory yet and takes a while to load
    When   the app's client asks it for an answer
    Then   the run is told that the model is being loaded, naming it, and the answer is not
           marked as one that waited: loading is this model's own time, and a load figure
    """
    fake.install("b:1b")
    fake.loading_for("b:1b", 0.8)
    client = OllamaClient(fake.url)
    details: list[object] = []
    client.detail_sink = details.append

    assert client.chat("b:1b", "hi") == CHAT_ANSWER

    assert details
    first = details[0]
    assert first.kind is WaitKind.LOADING
    assert first.model == "b:1b"
    assert client.last_stats is not None
    assert not client.last_stats.waited


def test_a_proofreading_answer_passes_the_apps_client(fake: FakeOllama) -> None:
    """
    Given  a fake that proofreads, answering all three steps of the exchange
    When   the app's client sends them, framed by the exchange's own prompt code
    Then   the corrected text, one note per edit and no unclear item come back, so the
           walkthrough can let a teacher proofread against this server
    """
    fake.answer_chats(
        ProofreadingAnswers(edits=(("habe", "hat"),), explanation="The verb was corrected.")
    )
    client = OllamaClient(fake.url)
    schema = explain_schema([1, 2])

    corrected = client.chat("m:1b", correct_user("Ich habe Hunger.", language="German", level="A2"))
    explained = client.chat(
        "m:1b",
        explain_user("1. habe → hat", language="German", level="A2"),
        format=schema.model_json_schema(),
    )
    unclear = client.chat(
        "m:1b",
        unclear_user("Ich hat Hunger.", language="German", level="A2"),
        format=UnclearAnswer.model_json_schema(),
    )

    assert corrected == "Ich hat Hunger."
    answer, error = parse_explain(explained)
    assert error == ""
    assert answer is not None
    assert [(note.id, note.category, note.explanation) for note in answer.edits] == [
        (1, PROOFREADING_CATEGORY, "The verb was corrected."),
        (2, PROOFREADING_CATEGORY, "The verb was corrected."),
    ]
    assert parse_unclear(unclear)[0] == UnclearAnswer(unclear=[])


def test_a_batch_of_one_edit_is_explained_too(fake: FakeOllama) -> None:
    """
    Given  a fake that proofreads, and a batch of one edit as the exchange sends it
    When   the app's client asks for the explanation of that one edit
    Then   the note comes back for that edit, so a document with a single change is explained
    """
    fake.answer_chats(
        ProofreadingAnswers(
            edits=(("habe", "hat"),), explanation="The verb was corrected.", category="grammar"
        )
    )
    client = OllamaClient(fake.url)

    explained = client.chat(
        "m:1b",
        explain_user("1. habe → hat", language="German", level="A2"),
        format=explain_schema([7]).model_json_schema(),
    )

    answer, error = parse_explain(explained)
    assert error == ""
    assert answer is not None
    assert [(note.id, note.category) for note in answer.edits] == [(7, "grammar")]


def test_a_request_without_a_text_is_refused(fake: FakeOllama) -> None:
    """
    Given  a fake that proofreads
    When   a message arrives that the exchange's framing does not appear in
    Then   the stand-in raises instead of answering with a text it did not read, so a changed
           framing is caught here and not as a wrong correction in the walk
    """
    fake.answer_chats(ProofreadingAnswers(edits=(("habe", "hat"),)))

    with pytest.raises(ValueError, match="carries no text"):
        fake.chat_answer({"messages": [{"role": "user", "content": "Correct this, please."}]})


def test_the_last_word_of_every_text_is_replaced_whatever_it_is(fake: FakeOllama) -> None:
    """
    Given  a fake that replaces the last word of a text, and texts a reader may have misread
    When   the exchange's step ① asks for them
    Then   each comes back with its last word replaced and the white space after it kept
    """
    fake.answer_chats(ProofreadingAnswers(last_word="Zielwort"))
    client = OllamaClient(fake.url)

    one = client.chat("m:1b", correct_user("Hello Wor1d", language="English", level="A2"))
    two = client.chat(
        "m:1b", correct_user("First Page\n\nSecond Page\n", language="English", level="A2")
    )

    assert one == "Hello Zielwort"
    assert two == "First Page\n\nSecond Zielwort\n"


def test_a_stand_in_that_cannot_answer_says_why_over_http(fake: FakeOllama) -> None:
    """
    Given  a fake that proofreads and a chat request without any text to proofread
    When   the request is sent
    Then   the answer is a 500 that carries the reason, not a connection that drops
    """
    fake.answer_chats(ProofreadingAnswers(last_word="Zielwort"))
    request = Request(
        f"{fake.url}/api/chat",
        data=json.dumps(
            {"model": "m:1b", "messages": [{"role": "user", "content": "Hi"}]}
        ).encode(),
        method="POST",
    )

    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=5)  # the fake answers on loopback

    assert caught.value.code == 500
    assert "carries no text" in caught.value.read().decode()
